"""Autorisation OAuth pilotée depuis l'interface (FIRST-002, CLOUD-003, P9).

Le §1.4 exige qu'aucune étape du parcours normal ne passe par un terminal.
La méthode documentée par rclone — installer rclone sur son poste, lancer
``rclone authorize`` et traverser une quinzaine de questions — ne tient pas
cette promesse. Ce module la remplace.

**Comment ça marche.** ``rclone authorize`` ouvre un serveur local sur le
port 53682 qui rend deux services : ``/auth`` redirige le navigateur vers la
page de consentement du fournisseur, et la racine reçoit le code en retour.
Les deux vivent donc *dans le conteneur*, et il suffit que le navigateur de
l'utilisateur puisse joindre ce port.

**Le serveur de rclone n'écoute que sur ``127.0.0.1``**, l'interface interne
du conteneur : publier le port 53682 ne suffit pas, le trafic arrive sur
l'interface réseau où personne n'écoute. On relaie donc ``/auth`` depuis
notre propre port, déjà publié — rclone n'y répond qu'une redirection vers
le fournisseur, qu'il suffit de transmettre.

**Ce qui résiste.** L'URL de redirection enregistrée par rclone chez les
fournisseurs est ``http://localhost:53682/``. Après consentement, le
navigateur revient donc sur *sa propre* machine, pas sur le serveur, et
tombe sur une page d'erreur. Rien ne permet de contourner cela sans
enregistrer notre propre application OAuth chez chaque fournisseur, avec le
domaine public et la revue que cela suppose.

D'où le seul geste demandé : recopier l'adresse de cette page d'erreur. On
la rejoue vers rclone, à l'intérieur du conteneur, et l'autorisation
s'achève. Un copier-coller au lieu d'une installation.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

#: Port sur lequel ``rclone authorize`` sert la page de départ et reçoit le
#: retour. Il est figé côté rclone : les URL de redirection enregistrées
#: chez les fournisseurs le désignent nommément.
AUTHORIZE_PORT = 53682

LINK_PATTERN = re.compile(r"Please go to the following link:\s*(\S+)")
#: rclone encadre le jeton de deux lignes de tirets.
TOKEN_PATTERN = re.compile(r"\{[^{}]*\"access_token\".*\}", re.DOTALL)

START_TIMEOUT = 20.0
COMPLETE_TIMEOUT = 60.0
SESSION_LIFETIME = 900.0  # un quart d'heure pour se connecter


class OAuthError(RuntimeError):
    """Échec de l'autorisation, message destiné à l'utilisateur."""


@dataclass
class OAuthSession:
    id: str
    provider: str
    process: subprocess.Popen[str]
    auth_path: str
    started_at: float = field(default_factory=time.time)
    output: list[str] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return time.time() - self.started_at > SESSION_LIFETIME


class OAuthBroker:
    """Conduit les autorisations, une à la fois.

    Le port 53682 étant unique, deux autorisations simultanées se
    marcheraient dessus : la seconde est refusée plutôt que de produire un
    échec incompréhensible.
    """

    def __init__(self, binary: str) -> None:
        self._binary = binary
        self._lock = threading.Lock()
        self._session: OAuthSession | None = None

    # -- démarrage ----------------------------------------------------------

    def start(self, provider: str) -> dict[str, str]:
        """Lance l'autorisation et rend le lien à ouvrir dans le navigateur.

        Le lien pointe sur **notre** application, qui relaiera la redirection :
        celui que rclone imprime désigne ``127.0.0.1:53682``, joignable du seul
        intérieur du conteneur.
        """
        with self._lock:
            self._discard_locked()

            process = subprocess.Popen(
                [self._binary, "authorize", provider, "--auth-no-open-browser"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
            )

            link = self._await_link(process)
            if link is None:
                process.kill()
                raise OAuthError(
                    "rclone n'a pas produit de lien d'autorisation ; le "
                    f"fournisseur « {provider} » en accepte-t-il un ?"
                )

            session = OAuthSession(
                id=f"{int(time.time())}-{provider}",
                provider=provider,
                process=process,
                auth_path=link,
            )
            self._session = session

        return {"session_id": session.id, "state": _state_of(link)}

    def _await_link(self, process: subprocess.Popen[str]) -> str | None:
        deadline = time.time() + START_TIMEOUT
        stream = process.stdout
        if stream is None:  # pragma: no cover - stdout est toujours capturé
            return None
        while time.time() < deadline:
            line = stream.readline()
            if not line:
                return None
            if match := LINK_PATTERN.search(line):
                return match.group(1)
        return None

    # -- achèvement ---------------------------------------------------------

    def complete(self, session_id: str, redirect_url: str) -> str:
        """Rejoue l'adresse de retour vers rclone et rend le jeton."""
        with self._lock:
            session = self._session
            if session is None or session.id != session_id:
                raise OAuthError(
                    "cette autorisation n'est plus en cours — relancez-la"
                )
            if session.expired:
                self._discard_locked()
                raise OAuthError("l'autorisation a expiré — relancez-la")

            query = urllib.parse.urlparse(redirect_url.strip()).query
            if not query or "code=" not in query:
                raise OAuthError(
                    "cette adresse ne contient pas de code d'autorisation ; "
                    "copiez la barre d'adresse entière de la page d'erreur"
                )

            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{AUTHORIZE_PORT}/?{query}", timeout=15
                ) as response:
                    response.read(2048)
            except urllib.error.HTTPError:
                # rclone répond parfois en erreur tout en ayant retenu le
                # code : c'est la sortie du processus qui fait foi.
                pass
            except (urllib.error.URLError, OSError) as exc:
                raise OAuthError(f"rclone n'écoute plus : {exc}") from exc

            token = self._await_token(session)
            self._discard_locked()

        if token is None:
            raise OAuthError(
                "le fournisseur n'a pas délivré de jeton ; vérifiez que "
                "l'autorisation a bien été accordée"
            )
        return token

    def _await_token(self, session: OAuthSession) -> str | None:
        deadline = time.time() + COMPLETE_TIMEOUT
        stream = session.process.stdout
        collected: list[str] = []
        while time.time() < deadline and stream is not None:
            line = stream.readline()
            if not line:
                break
            collected.append(line)
            if match := TOKEN_PATTERN.search("".join(collected)):
                return match.group(0).strip()
        return None

    # -- cycle de vie -------------------------------------------------------

    def cancel(self) -> None:
        with self._lock:
            self._discard_locked()

    def _discard_locked(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        try:
            session.process.kill()
            session.process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            pass


def _state_of(link: str) -> str:
    """Jeton anti-rejeu que rclone attend en retour."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    values = query.get("state") or [""]
    return values[0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[override]
        return None


def provider_redirect(state: str, timeout: float = 15.0) -> str:
    """Adresse de consentement du fournisseur, obtenue de rclone.

    rclone répond une redirection ; on la transmet au navigateur plutôt que
    de le faire parler directement à un port qu'il ne peut pas joindre.
    """
    url = f"http://127.0.0.1:{AUTHORIZE_PORT}/auth?{urllib.parse.urlencode({'state': state})}"
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=timeout) as response:
            raise OAuthError(
                f"rclone n'a pas redirigé (HTTP {response.status}) — "
                "l'autorisation a peut-être expiré"
            )
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location")
        if exc.code in (301, 302, 303, 307, 308) and location:
            return location
        raise OAuthError(f"rclone a répondu HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OAuthError(
            "aucune autorisation n'est en cours — relancez-la"
        ) from exc


GRAPH_ENDPOINTS = (
    "https://graph.microsoft.com/v1.0/me/drive",
    # Le dialogue de rclone interroge la forme plurielle ; certains comptes
    # professionnels ne répondent qu'à celle-là.
    "https://graph.microsoft.com/v1.0/me/drives",
)


def access_token_of(blob: str) -> str:
    """Jeton d'accès contenu dans le bloc rendu par ``rclone authorize``."""
    import json

    try:
        payload = json.loads(blob)
    except ValueError as exc:
        raise OAuthError(
            "le bloc rendu par rclone n'est pas lisible comme du JSON"
        ) from exc
    token = str(payload.get("access_token", "")) if isinstance(payload, dict) else ""
    if not token:
        raise OAuthError("le bloc rendu par rclone ne contient pas d'access_token")
    return token


def describe_drive(access_token: str, timeout: float = 15.0) -> dict[str, str]:
    """Identifie le disque OneDrive associé à un jeton.

    OneDrive exige ``drive_id`` et ``drive_type`` en plus du jeton, valeurs
    que le dialogue interactif de rclone découvre en interrogeant Microsoft.
    On fait la même chose, pour éviter de renvoyer l'utilisateur au terminal.

    Deux points d'entrée sont tentés : le disque par défaut, puis la liste.
    Les comptes professionnels et les comptes personnels ne répondent pas
    toujours au même.
    """
    import json

    causes: list[str] = []
    for endpoint in GRAPH_ENDPOINTS:
        request = urllib.request.Request(
            endpoint, headers={"Authorization": f"Bearer {access_token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = _graph_error(exc)
            causes.append(f"{endpoint.rsplit('/', 1)[-1]} : {detail}")
            continue
        except (urllib.error.URLError, OSError, ValueError) as exc:
            causes.append(f"{endpoint.rsplit('/', 1)[-1]} : {exc}")
            continue

        drive = payload
        if isinstance(payload, dict) and "value" in payload:
            entries = payload.get("value") or []
            if not entries:
                causes.append("aucun disque associé à ce compte")
                continue
            drive = entries[0]

        drive_id = str(drive.get("id", "")) if isinstance(drive, dict) else ""
        if drive_id:
            drive_type = str(drive.get("driveType", "")) or "personal"
            return {"drive_id": drive_id, "drive_type": drive_type}
        causes.append("réponse sans identifiant de disque")

    raise OAuthError("Microsoft n'a pas identifié le disque — " + " ; ".join(causes))


def _graph_error(exc: urllib.error.HTTPError) -> str:
    """Message d'erreur de Graph, plutôt qu'un simple code (§27.10)."""
    import json

    try:
        payload = json.loads(exc.read(4096).decode("utf-8", "replace"))
        message = payload.get("error", {}).get("message")
        if message:
            return f"HTTP {exc.code} — {message}"
    except Exception:  # pragma: no cover - corps absent ou illisible
        pass
    if exc.code == 401:
        return "HTTP 401 — jeton refusé"
    if exc.code == 403:
        return "HTTP 403 — autorisation insuffisante sur les fichiers"
    return f"HTTP {exc.code}"
