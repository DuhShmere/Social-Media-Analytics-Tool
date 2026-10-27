import pytest

from redaction import redact_secrets
from token_vault import (
    TokenVaultError,
    decrypt_token,
    encrypt_token,
    generate_key,
)


# ---------------------------------------------------------
# Token encryption
# ---------------------------------------------------------

def test_tokens_round_trip_and_are_not_stored_in_plain_text():
    key = generate_key()
    token = "IGAAexample-long-lived-token"

    encrypted = encrypt_token(token, key)

    assert token not in encrypted
    assert decrypt_token(encrypted, key) == token


def test_encrypting_twice_gives_different_ciphertext():
    # Fernet adds a random IV, so equal tokens are not recognizable.
    key = generate_key()

    assert encrypt_token("same-token", key) != encrypt_token("same-token", key)


def test_wrong_key_asks_the_user_to_reconnect():
    encrypted = encrypt_token("token", generate_key())

    with pytest.raises(TokenVaultError, match="Reconnect the account"):
        decrypt_token(encrypted, generate_key())


def test_tampered_token_is_rejected():
    key = generate_key()
    encrypted = encrypt_token("token", key)
    tampered = encrypted[:-4] + ("AAAA" if not encrypted.endswith("AAAA") else "BBBB")

    with pytest.raises(TokenVaultError):
        decrypt_token(tampered, key)


@pytest.mark.parametrize(
    ("key", "message"),
    [
        (None, "TOKEN_ENCRYPTION_KEY is not set"),
        ("", "TOKEN_ENCRYPTION_KEY is not set"),
        ("not-a-valid-fernet-key", "not a valid key"),
    ],
    ids=["missing", "empty", "invalid"],
)
def test_missing_or_invalid_key_gives_a_clear_error(key, message):
    with pytest.raises(TokenVaultError, match=message):
        encrypt_token("token", key)


# ---------------------------------------------------------
# Secret redaction in logs
# ---------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "GET https://graph.instagram.com/me/media?fields=id&access_token=IGAAsecret123",
            "GET https://graph.instagram.com/me/media?fields=id&access_token=REDACTED",
        ),
        (
            "https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=abc123&access_token=IGAAx",
            "https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=REDACTED&access_token=REDACTED",
        ),
        (
            "callback ?code=AQBx9-oneTimeCode&state=xyz",
            "callback ?code=REDACTED&state=xyz",
        ),
        (
            "https://youtube.googleapis.com/v3/channels?part=snippet&key=AIzaSecret",
            "https://youtube.googleapis.com/v3/channels?part=snippet&key=REDACTED",
        ),
    ],
    ids=["instagram-access-token", "client-secret", "oauth-code", "youtube-key"],
)
def test_secret_url_parameters_are_redacted(text, expected):
    assert redact_secrets(text) == expected


def test_similar_parameter_names_are_left_alone():
    # Only exact parameter names are secrets; error_code is not.
    text = "/oauth/callback?error_code=200&error_reason=user_denied"

    assert redact_secrets(text) == text
