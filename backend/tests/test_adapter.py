"""Adaptateur rclone — exécuté contre le binaire réel (§19, §20.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from csm.rclone.adapter import RcloneAdapter, RcloneError, RcloneUnavailable
from csm.rclone.providers import (
    PRIORITY_PROVIDERS,
    hidden_fields,
    password_fields,
    sensitive_fields,
)


@pytest.fixture
def adapter(rclone_path: str, tmp_path: Path) -> RcloneAdapter:
    return RcloneAdapter(binary=rclone_path, config_path=tmp_path / "rclone.conf")


def test_version(adapter: RcloneAdapter) -> None:
    info = adapter.version()
    assert info.version.startswith("v")
    assert info.arch


def test_missing_binary_raises(tmp_path: Path) -> None:
    broken = RcloneAdapter(
        binary=str(tmp_path / "absent"), config_path=tmp_path / "rclone.conf"
    )
    with pytest.raises(RcloneUnavailable):
        broken.version()


def test_all_priority_providers_exist(adapter: RcloneAdapter) -> None:
    """Les sept fournisseurs du §9.1 doivent être fournis par le moteur."""
    available = {provider["Name"] for provider in adapter.providers()}
    assert set(PRIORITY_PROVIDERS) <= available


def test_password_fields_come_from_the_catalogue(adapter: RcloneAdapter) -> None:
    sftp = next(p for p in adapter.providers() if p["Name"] == "sftp")
    assert "pass" in password_fields(sftp)
    assert "host" not in password_fields(sftp)

    s3 = next(p for p in adapter.providers() if p["Name"] == "s3")
    # La clé secrète S3 n'est pas un « password » rclone mais reste un
    # identifiant : elle ne doit jamais être renvoyée par l'API.
    assert "secret_access_key" in hidden_fields(s3)


def test_connection_coordinates_stay_visible(adapter: RcloneAdapter) -> None:
    """rclone marque host et user « Sensitive » pour ses rapports de bogue.

    Les masquer dans l'UI empêcherait l'utilisateur de reconnaître son
    propre serveur au moment de le modifier — le §6.1 ne vise que les
    secrets, jetons et mots de passe.
    """
    sftp = next(p for p in adapter.providers() if p["Name"] == "sftp")
    assert {"host", "user"} <= sensitive_fields(sftp)
    assert not ({"host", "user"} & hidden_fields(sftp))
    assert {"pass", "key_pem"} <= hidden_fields(sftp)


def test_single_letter_remote_names_are_avoided() -> None:
    """Sous Windows, « u: » désigne le lecteur U: avant le remote « u »."""
    from csm.services.remotes import slugify_remote_name

    assert len(slugify_remote_name("A")) >= 2
    assert len(slugify_remote_name("é")) >= 2
    assert slugify_remote_name("Google Drive perso") == "Google-Drive-perso"


def test_obscure_never_leaks_and_roundtrips(adapter: RcloneAdapter) -> None:
    secret = "MonMotDePasse!42%éà"
    obscured = adapter.obscure(secret)

    assert obscured
    assert secret not in obscured

    revealed = adapter.run(["reveal", obscured], use_config=False)
    assert revealed.returncode == 0
    assert revealed.stdout.strip() == secret


def test_lsjson_on_an_alias_remote(adapter: RcloneAdapter, tmp_path: Path) -> None:
    data = tmp_path / "données"
    (data / "Photos").mkdir(parents=True)
    (data / "note.txt").write_text("bonjour", encoding="utf-8")

    adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
    adapter.config_path.write_text(
        f"[essai]\ntype = alias\nremote = {data}\n", encoding="utf-8"
    )

    entries = adapter.lsjson("essai:")
    names = {entry["Name"] for entry in entries}
    assert names == {"Photos", "note.txt"}

    directories = adapter.lsjson("essai:", dirs_only=True)
    assert {entry["Name"] for entry in directories} == {"Photos"}


def test_unknown_remote_keeps_the_technical_cause(adapter: RcloneAdapter) -> None:
    """§27.10 — ne jamais réduire une erreur rclone à « Échec »."""
    adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
    adapter.config_path.write_text("", encoding="utf-8")

    with pytest.raises(RcloneError) as excinfo:
        adapter.lsjson("inconnu:")

    message = str(excinfo.value)
    assert message
    assert "inconnu" in message.lower() or "not found" in message.lower()


def test_unicode_and_spaces_in_paths(adapter: RcloneAdapter, tmp_path: Path) -> None:
    """GLOBAL-005 — noms Unicode, espaces et caractères spéciaux."""
    data = tmp_path / "mes données"
    (data / "dossier accentué éàü").mkdir(parents=True)
    (data / "fichier avec espaces.txt").write_text("x", encoding="utf-8")

    adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
    adapter.config_path.write_text(
        f"[unicode]\ntype = alias\nremote = {data}\n", encoding="utf-8"
    )

    names = {entry["Name"] for entry in adapter.lsjson("unicode:")}
    assert names == {"dossier accentué éàü", "fichier avec espaces.txt"}


def test_the_environment_cannot_alter_the_command(
    adapter: RcloneAdapter, monkeypatch
) -> None:
    """§18 — la ligne de commande doit être la vérité entière.

    rclone lit toute variable ``RCLONE_*`` comme une option. Sans nettoyage,
    une variable posée dans le template Unraid modifierait silencieusement
    une opération destructive : ``RCLONE_DELETE_EXCLUDED`` supprimerait à
    destination des fichiers que nos filtres viennent d'écarter.
    """
    monkeypatch.setenv("RCLONE_DELETE_EXCLUDED", "true")
    monkeypatch.setenv("RCLONE_MAX_DELETE", "99999")

    environment = adapter._environment()

    assert "RCLONE_DELETE_EXCLUDED" not in environment
    assert "RCLONE_MAX_DELETE" not in environment
    # Les deux seuls réglages que nous posons nous-mêmes survivent.
    assert environment["RCLONE_ASK_PASSWORD"] == "false"
    assert "PATH" in environment, "le reste de l'environnement doit être conservé"


def test_a_stray_rclone_variable_no_longer_breaks_everything(
    adapter: RcloneAdapter, monkeypatch
) -> None:
    """Le cas réel : RCLONE_VERSION=1.75.1 posée par une chaîne de CI.

    rclone la lisait comme le drapeau booléen ``--version`` et refusait de
    démarrer, sur *toutes* les commandes.
    """
    monkeypatch.setenv("RCLONE_VERSION", "1.75.1")
    assert adapter.version().version.startswith("v")
