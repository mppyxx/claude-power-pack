import json
import unittest

import app


class AppTest(unittest.TestCase):
    def test_health(self):
        status, _, body = app.handle("GET", "/health")
        self.assertEqual((status, body), (200, b"ok"))

    def test_profile_page(self):
        status, ctype, body = app.handle("GET", "/profile/alice")
        self.assertEqual(status, 200)
        self.assertIn(b"Alice Chen", body)

    def test_unknown_profile(self):
        status, _, _ = app.handle("GET", "/profile/nobody")
        self.assertEqual(status, 404)

    def test_download_shared_file(self):
        status, _, body = app.handle("GET", "/files/handbook.txt")
        self.assertEqual(status, 200)
        self.assertIn(b"Lumen team handbook", body)

    def test_me_requires_login(self):
        status, _, _ = app.handle("GET", "/me")
        self.assertEqual(status, 401)

    def test_me_with_token(self):
        token = app.sign_token("bob")
        status, _, body = app.handle("GET", "/me", {"Authorization": f"Bearer {token}"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["username"], "bob")


if __name__ == "__main__":
    unittest.main()
