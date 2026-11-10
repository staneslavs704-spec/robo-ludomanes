import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode
from app.security import validate_init_data


class TelegramInitDataTests(unittest.TestCase):
    def make_init_data(self, token: str, user_id: int):
        data = {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
        }
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return urlencode(data)

    def test_valid_data(self):
        token = "123456:TEST_TOKEN"
        result = validate_init_data(self.make_init_data(token, 12345), token, 86400)
        self.assertEqual(result["user"]["id"], 12345)

    def test_tampered_data_rejected(self):
        token = "123456:TEST_TOKEN"
        raw = self.make_init_data(token, 12345).replace("12345", "99999")
        with self.assertRaises(Exception):
            validate_init_data(raw, token, 86400)


if __name__ == "__main__":
    unittest.main()
