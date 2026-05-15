import hashlib
import hmac
import unittest

from security import is_valid_discourse_signature


class SecurityTests(unittest.TestCase):
    def test_valid_discourse_signature(self):
        raw_body = b'{"notification":{"user_id":123}}'
        secret = "secret"
        signature = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

        self.assertTrue(is_valid_discourse_signature(raw_body, signature, secret))

    def test_invalid_discourse_signature(self):
        self.assertFalse(is_valid_discourse_signature(b"{}", "sha256=bad", "secret"))


if __name__ == "__main__":
    unittest.main()

