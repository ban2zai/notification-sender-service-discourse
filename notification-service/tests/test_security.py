import hashlib
import hmac
import unittest

from security import is_valid_bearer_token, is_valid_discourse_signature


class SecurityTests(unittest.TestCase):
    def test_valid_discourse_signature(self):
        raw_body = b'{"notification":{"user_id":123}}'
        secret = "secret"
        signature = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

        self.assertTrue(is_valid_discourse_signature(raw_body, signature, secret))

    def test_invalid_discourse_signature(self):
        self.assertFalse(is_valid_discourse_signature(b"{}", "sha256=bad", "secret"))

    def test_valid_bearer_token(self):
        self.assertTrue(is_valid_bearer_token("Bearer secret", "secret"))

    def test_invalid_bearer_token(self):
        self.assertFalse(is_valid_bearer_token("Bearer bad", "secret"))
        self.assertFalse(is_valid_bearer_token("secret", "secret"))
        self.assertFalse(is_valid_bearer_token("Bearer secret", ""))


if __name__ == "__main__":
    unittest.main()
