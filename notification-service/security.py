import hashlib
import hmac


def is_valid_discourse_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    expected = "sha256=" + hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_valid_bearer_token(authorization: str, expected_token: str) -> bool:
    if not expected_token:
        return False

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False

    token = authorization[len(prefix) :]
    return hmac.compare_digest(token, expected_token)
