import hashlib
import hmac
import json
import os
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import server


ORIGIN = "https://23e4rtyuyf.github.io"
TEST_PASSWORD = "correct horse battery staple - test only"
TEST_SENDER_ID = "6f8f7d3e-bbe4-4dbb-9084-7f33a0c2d8fc"


class ServerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.SiteHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.env = patch.dict(os.environ, {
            "CHAT_ACCESS_PASSWORD": TEST_PASSWORD,
            "SESSION_SECRET": "test-session-secret-do-not-use",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-do-not-use",
            "CHAT_ALLOWED_ORIGINS": ORIGIN,
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        with server._lock:
            server._challenges.clear()
            server._sessions.clear()
            server._failed_logins.clear()
        self.saved_message = None
        self.supabase = patch.object(server, "_supabase_request", side_effect=self.fake_supabase)
        self.auth_user = patch.object(server, "_create_anonymous_user", return_value=TEST_SENDER_ID)
        self.supabase.start()
        self.auth_user.start()
        self.addCleanup(self.supabase.stop)
        self.addCleanup(self.auth_user.stop)

    def fake_supabase(self, path, method="GET", body=None, query=None):
        if method == "GET":
            return [{
                "id": 1,
                "created_at": "2026-11-10T12:00:00Z",
                "sender_email": "Test",
                "encrypted_text": "PM3." + "A" * 45,
            }]
        self.saved_message = body
        return None

    def request(self, path, method="GET", payload=None, token=None, origin=ORIGIN):
        headers = {"Origin": origin}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read() or b"{}")
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def sign_in(self):
        status_code, challenge = self.request("/api/auth/challenge")
        self.assertEqual(status_code, 200)
        nonce = challenge["nonce"]
        import base64
        nonce_bytes = base64.urlsafe_b64decode(nonce + "=" * ((4 - len(nonce) % 4) % 4))
        key = hashlib.pbkdf2_hmac(
            "sha256", TEST_PASSWORD.encode(), server.AUTH_SALT, server.AUTH_ITERATIONS
        )
        proof = hmac.new(key, nonce_bytes, hashlib.sha256).hexdigest()
        status_code, session = self.request(
            "/api/auth/verify",
            method="POST",
            payload={"challengeId": challenge["challengeId"], "proof": proof},
        )
        self.assertEqual(status_code, 200)
        return session["token"]

    def test_chat_api_rejects_unauthenticated_and_unapproved_origins(self):
        status_code, payload = self.request("/api/messages?room=123456")
        self.assertEqual(status_code, 401)
        self.assertIn("Sign in", payload["error"])

        status_code, _ = self.request(
            "/api/auth/challenge", origin="https://attacker.example"
        )
        self.assertEqual(status_code, 403)

    def test_github_pages_preflight_allows_bearer_api_requests(self):
        request = Request(
            f"{self.base_url}/api/messages",
            method="OPTIONS",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
            self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])

    def test_password_proof_is_one_time_and_invalid_proof_is_rejected(self):
        status_code, challenge = self.request("/api/auth/challenge")
        self.assertEqual(status_code, 200)
        status_code, payload = self.request(
            "/api/auth/verify",
            method="POST",
            payload={"challengeId": challenge["challengeId"], "proof": "0" * 64},
        )
        self.assertEqual(status_code, 401)
        self.assertEqual(payload["error"], "Incorrect access password.")

        status_code, payload = self.request(
            "/api/auth/verify",
            method="POST",
            payload={"challengeId": challenge["challengeId"], "proof": "0" * 64},
        )
        self.assertEqual(status_code, 401)
        self.assertEqual(payload["error"], "Sign-in expired. Try again.")

    def test_expired_session_is_rejected(self):
        token = self.sign_in()
        with server._lock:
            server._sessions[server._session_key(token)]["expires"] = time.time() - 1
        status_code, payload = self.request("/api/messages?room=123456", token=token)
        self.assertEqual(status_code, 401)
        self.assertIn("expired", payload["error"])

    def test_authenticated_chat_read_and_write_use_server_identity(self):
        token = self.sign_in()
        status_code, response = self.request("/api/messages?room=123456", token=token)
        self.assertEqual(status_code, 200)
        self.assertEqual(response["messages"][0]["sender_email"], "Test")

        status_code, _ = self.request(
            "/api/messages",
            method="POST",
            token=token,
            payload={
                "sender_id": "attacker-controlled-value",
                "sender_email": "Test",
                "receiver_email": "123456",
                "encrypted_text": "PM3." + "B" * 45,
            },
        )
        self.assertEqual(status_code, 201)
        self.assertEqual(self.saved_message["sender_id"], TEST_SENDER_ID)
        self.assertNotEqual(self.saved_message["sender_id"], "attacker-controlled-value")

    def test_server_rejects_unencrypted_or_invalid_room_messages(self):
        token = self.sign_in()
        status_code, _ = self.request(
            "/api/messages",
            method="POST",
            token=token,
            payload={
                "sender_email": "Test",
                "receiver_email": "not-a-room",
                "encrypted_text": "plaintext",
            },
        )
        self.assertEqual(status_code, 400)
        self.assertIsNone(self.saved_message)


if __name__ == "__main__":
    unittest.main()
