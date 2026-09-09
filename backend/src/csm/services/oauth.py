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

    def start(self, provider: str, public_host: str) -> dict[str, str]:
        """Lance l'autorisation et rend le lien à ouvrir dans le navigateur.

        ``public_host`` est l'adresse par laquelle l'utilisateur joint
        l'application : le lien que rclone imprime désigne ``127.0.0.1``,
        c'est-à-dire le conteneur, et serait inutilisable tel quel.
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

        return {
            "session_id": session.id,
            "auth_url": _rewrite_host(link, public_host),
        }

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


def _rewrite_host(link: str, public_host: str) -> str:
    """Remplace ``127.0.0.1`` par l'adresse que le navigateur peut joindre."""
    parsed = urllib.parse.urlparse(link)
    host = public_host.split(":")[0]
    return urllib.parse.urlunparse(
        parsed._replace(netloc=f"{host}:{AUTHORIZE_PORT}")
    )


def describe_drive(access_token: str, timeout: float = 15.0) -> dict[str, str]:
    """Identifie le disque OneDrive associé à un jeton.

    OneDrive exige ``drive_id`` et ``drive_type`` en plus du jeton, valeurs
    que le dialogue interactif de rclone découvre en interrogeant Microsoft.
    On fait la même chose, pour éviter de renvoyer l'utilisateur au terminal.
    """
    request = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/me/drive",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            import json

            payload = json.load(response)
    except Exception as exc:  # pragma: no cover - dépend du réseau
        raise OAuthError(f"disque OneDrive introuvable : {exc}") from exc

    drive_id = str(payload.get("id", ""))
    drive_type = str(payload.get("driveType", ""))
    if not drive_id:
        raise OAuthError("Microsoft n'a pas renvoyé d'identifiant de disque")
    return {"drive_id": drive_id, "drive_type": drive_type or "personal"}
