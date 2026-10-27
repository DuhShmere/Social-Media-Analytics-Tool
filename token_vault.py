"""Encrypt OAuth access tokens before they are stored in the database.

Uses Fernet (AES with an authentication check), so a modified or
corrupted token is rejected instead of being decrypted into garbage.
The key comes from TOKEN_ENCRYPTION_KEY. There is no random fallback:
a new key on every start would make every saved token unreadable.
"""

from cryptography.fernet import Fernet, InvalidToken


class TokenVaultError(Exception):
    """A token could not be stored or read. The message is user-safe."""


def generate_key():
    return Fernet.generate_key().decode("ascii")


def _fernet(key):
    if not key:
        raise TokenVaultError(
            "TOKEN_ENCRYPTION_KEY is not set, so access tokens cannot be "
            "stored. Add it to your .env file."
        )

    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError):
        raise TokenVaultError(
            "TOKEN_ENCRYPTION_KEY is not a valid key. Generate one with: "
            "python -c \"from token_vault import generate_key; "
            "print(generate_key())\""
        ) from None


def encrypt_token(token, key):
    return _fernet(key).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(encrypted_token, key):
    try:
        return _fernet(key).decrypt(
            encrypted_token.encode("ascii")
        ).decode("utf-8")
    except InvalidToken:
        raise TokenVaultError(
            "The saved access token could not be read. This happens if "
            "TOKEN_ENCRYPTION_KEY changed. Reconnect the account."
        ) from None
