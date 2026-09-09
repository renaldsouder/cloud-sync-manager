"""Protection de l'interface Web (SEC-006, §18, P11).

Un mot de passe unique, pas de comptes multiples : le §21 place la gestion
d'utilisateurs et d'équipes hors périmètre, et un NAS domestique n'en a pas
besoin. Ce qu'il faut, c'est que le premier venu sur le réseau ne puisse pas
déclencher une suppression.

Deux choix méritent d'être expliqués.

**Empreinte par ``scrypt``**, de la bibliothèque standard, plutôt qu'Argon2id
qui exigerait une dépendance supplémentaire. Pour un mot de passe unique
gardé localement, une fonction de dérivation coûteuse en mémoire suffit
largement, et le §28 invite à ne pas ajouter de brique sans nécessité.

**Jeton de session signé, sans état serveur.** Le cookie porte sa propre date
d'expiration et une signature HMAC ; aucune table de sessions à maintenir, et
la session survit au redémarrage du conteneur. Changer la clé de signature
révoque immédiatement toutes les sessions ouvertes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

#: Paramètres scrypt : coûteux en mémoire (16 Mio) sans rendre la connexion
#: perceptiblement lente sur le matériel d'un NAS.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
SALT_LENGTH = 16

PREFIX = "scrypt"
SESSION_COOKIE = "csm_session"
DEFAULT_LIFETIME = 30 * 24 * 3600  # trente jours

MIN_PASSWORD_LENGTH = 8


class AuthError(ValueError):
    """Refus lié au mot de passe, message destiné à l'utilisateur."""


# -- mot de passe -------------------------------------------------------------


def hash_password(password: str) -> str:
    """Empreinte d'un mot de passe, sel compris, sous forme transportable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caractères"
        )
    salt = os.urandom(SALT_LENGTH)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_LENGTH,
    )
    return "$".join(
        [
            PREFIX,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(derived).decode(),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Comparaison en temps constant, insensible aux erreurs de format."""
    if not stored:
        return False
    try:
        prefix, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if prefix != PREFIX:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(hash_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, base64.b64decode(hash_b64))


# -- clé de signature ---------------------------------------------------------


def load_or_create_secret(path: Path) -> bytes:
    """Clé de signature des sessions, conservée dans l'appdata.

    Générée au premier démarrage. La supprimer révoque toutes les sessions —
    c'est le moyen de reprendre la main si un poste a été compromis.
    """
    if path.is_file():
        data = path.read_bytes().strip()
        if len(data) >= 32:
            return data

    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(48)
    path.write_bytes(secret)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - dépend du système de fichiers
        pass
    return secret


# -- jeton de session ---------------------------------------------------------


@dataclass(frozen=True)
class Session:
    expires_at: int

    @property
    def valid(self) -> bool:
        return self.expires_at > time.time()


def issue_token(secret: bytes, lifetime: int = DEFAULT_LIFETIME) -> str:
    expires_at = int(time.time()) + lifetime
    payload = str(expires_at).encode()
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    return f"{expires_at}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


def read_token(token: str | None, secret: bytes) -> Session | None:
    """Session portée par le jeton, ou ``None`` s'il est invalide ou périmé."""
    if not token or "." not in token:
        return None
    raw_expiry, _, raw_signature = token.partition(".")
    try:
        expires_at = int(raw_expiry)
    except ValueError:
        return None

    expected = hmac.new(secret, raw_expiry.encode(), hashlib.sha256).digest()
    padding = "=" * (-len(raw_signature) % 4)
    try:
        provided = base64.urlsafe_b64decode(raw_signature + padding)
    except (ValueError, TypeError):
        return None

    if not hmac.compare_digest(expected, provided):
        return None

    session = Session(expires_at=expires_at)
    return session if session.valid else None


# -- protection contre les essais répétés -------------------------------------


class LoginThrottle:
    """Ralentit les tentatives répétées depuis une même origine.

    Sans être une défense complète, cela suffit à rendre l'essai systématique
    de mots de passe impraticable sur un réseau domestique.
    """

    def __init__(self, allowed: int = 5, window: float = 300.0) -> None:
        self._allowed = allowed
        self._window = window
        self._attempts: dict[str, list[float]] = {}

    def blocked_for(self, origin: str) -> float:
        """Secondes restantes avant une nouvelle tentative, 0 si autorisée."""
        now = time.time()
        recent = [at for at in self._attempts.get(origin, []) if now - at < self._window]
        self._attempts[origin] = recent
        if len(recent) < self._allowed:
            return 0.0
        return round(self._window - (now - recent[0]), 1)

    def record_failure(self, origin: str) -> None:
        self._attempts.setdefault(origin, []).append(time.time())

    def clear(self, origin: str) -> None:
        self._attempts.pop(origin, None)
