"""SEC-002 — aucun secret ne doit franchir le filtre.

Les cas sont pris sur des formats réels : section rclone.conf, jeton OAuth
Google, URL signée S3, en-tête Authorization, URL SFTP avec mot de passe.
"""

from __future__ import annotations

import pytest

from csm.security.redaction import MASK, redact, redact_mapping

RCLONE_TOKEN = (
    '{"access_token":"ya29.a0AfB_byC3xK9","token_type":"Bearer",'
    '"refresh_token":"1//09xLkQ2mNpQrS","expiry":"2026-09-09T12:00:00Z"}'
)


@pytest.mark.parametrize(
    "leaky",
    [
        "pass = Xy7_obscured_value_here",
        "password: hunter2",
        "client_secret = GOCSPX-4kL0mNpQrStUvWx",
        "--api-key AKIAIOSFODNN7EXAMPLE",
        f"token = {RCLONE_TOKEN}",
        f'{{"refresh_token": "1//09xLkQ2mNpQrS"}}',
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "https://bucket.s3.amazonaws.com/f.txt?X-Amz-Signature=9f8e7d6c5b4a3210",
        "sftp://renald:MonMotDePasse@192.168.1.10/data",
    ],
)
def test_secret_never_survives(leaky: str) -> None:
    cleaned = redact(leaky)
    assert cleaned is not None
    assert MASK in cleaned
    for secret in (
        "hunter2",
        "GOCSPX-4kL0mNpQrStUvWx",
        "AKIAIOSFODNN7EXAMPLE",
        "ya29.a0AfB_byC3xK9",
        "1//09xLkQ2mNpQrS",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "9f8e7d6c5b4a3210",
        "MonMotDePasse",
        "Xy7_obscured_value_here",
    ):
        assert secret not in cleaned


def test_non_secrets_are_preserved() -> None:
    line = "Transferred: 12 fichiers, 3.4 MiB vers gdrive:Photos/2026"
    assert redact(line) == line


def test_mapping_masks_by_key_even_when_value_looks_harmless() -> None:
    payload = {
        "name": "Google Drive perso",
        "provider": "drive",
        "token": "abc",
        "nested": {"client_secret": "x", "remote_path": "Photos"},
        "runs": [{"password": "p"}],
    }
    cleaned = redact_mapping(payload)
    assert cleaned["name"] == "Google Drive perso"
    assert cleaned["nested"]["remote_path"] == "Photos"
    assert cleaned["token"] == MASK
    assert cleaned["nested"]["client_secret"] == MASK
    assert cleaned["runs"][0]["password"] == MASK


def test_empty_values_pass_through() -> None:
    assert redact(None) is None
    assert redact("") == ""
