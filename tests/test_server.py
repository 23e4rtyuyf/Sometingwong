import json
import os
import re
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import MagicMock, patch

import server


ORIGIN = "https://23e4rtyuyf.github.io"
TEST_SENDER_ID = "6f8f7d3e-bbe4-4dbb-9084-7f33a0c2d8fc"
TEST_SUPABASE_ACCESS_TOKEN = "a" * 128


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
            "SESSION_SECRET": "test-session-secret-do-not-use",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-do-not-use",
            "CHAT_ALLOWED_ORIGINS": ORIGIN,
            "CHAT_ADMIN_EMAILS": "admin@example.com",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        with server._lock:
            server._sessions.clear()
            server._room_join_failures.clear()
        self.saved_message = None
        self.messages = [{
            "id": 1,
            "created_at": "2026-11-10T12:00:00Z",
            "sender_email": "Test",
            "receiver_email": "123456",
            "sender_id": TEST_SENDER_ID,
            "encrypted_text": "PM3." + "A" * 45,
        }]
        self.rooms = {}
        self.security_events = []
        self.failed_logins = []
        self.legacy_room_codes = {"123456"}
        self.members = set()
        self.supabase = patch.object(server, "_supabase_request", side_effect=self.fake_supabase)
        self.auth_user_patch = patch.object(server, "_supabase_auth_user_details", return_value={
            "id": TEST_SENDER_ID,
            "email": "user@example.com",
            "app_metadata": {},
        })
        self.supabase.start()
        self.auth_user = self.auth_user_patch.start()
        self.addCleanup(self.supabase.stop)
        self.addCleanup(self.auth_user_patch.stop)

    def fake_supabase(self, path, method="GET", body=None, query=None, prefer="return=minimal"):
        query = query or {}
        table = path.rsplit("/", 1)[-1]
        room_code = query.get("room_code", "").removeprefix("eq.")
        sender_id = query.get("sender_id", "").removeprefix("eq.")
        if table == "chat_api_security_events":
            if method == "GET":
                rows = sorted(self.security_events, key=lambda row: row["created_at"], reverse=True)
                return rows[:int(query.get("limit", "100"))]
            if method == "POST":
                row = dict(body)
                row.setdefault("created_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
                self.security_events.append(row)
                return None
            if method == "DELETE":
                event_id = query.get("event_id", "").removeprefix("eq.")
                deleted = [row for row in self.security_events if row.get("event_id") == event_id]
                self.security_events[:] = [
                    row for row in self.security_events if row.get("event_id") != event_id
                ]
                return deleted
        if table == "chat_api_failed_logins":
            if method == "GET":
                address_hash = query.get("address_hash", "").removeprefix("eq.")
                cutoff = query.get("attempted_at", "").removeprefix("gt.")
                rows = [
                    row for row in self.failed_logins
                    if row["address_hash"] == address_hash and row["attempted_at"] > cutoff
                ]
                return rows[:int(query.get("limit", "100"))]
            if method == "POST":
                row = dict(body)
                row.setdefault("attempted_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
                self.failed_logins.append(row)
                return None
            if method == "DELETE":
                return None
        if table == "chat_api_rooms":
            if method == "GET":
                room = self.rooms.get(room_code)
                if room:
                    return [dict(room)]
                if room_code in self.legacy_room_codes:
                    return [{"room_code": room_code, "room_name": f"Room {room_code}"}]
                return []
            if method == "POST":
                room = dict(body)
                self.rooms[room["room_code"]] = room
                return [dict(room)] if prefer == "return=representation" else None
            if method == "PATCH":
                room = self.rooms.get(room_code)
                if not room:
                    return []
                room.update(body)
                return [dict(room)] if prefer == "return=representation" else None
        if table == "chat_api_room_members":
            if method == "GET":
                if not room_code:
                    return [
                        {"room_code": code, "joined_at": "2026-11-11T12:00:00Z"}
                        for code, member in self.members if member == sender_id
                    ]
                return [{"room_code": room_code}] if (room_code, sender_id) in self.members else []
            if method == "POST":
                self.members.add((body["room_code"], body["sender_id"]))
                return None
        if table == "messages":
            if (method == "GET" and query.get("select") == "id"
                and "sender_id" not in query and "order" not in query):
                return []
            if method == "GET":
                message_room = query.get("receiver_email", "").removeprefix("eq.")
                rows = [
                    dict(message) for message in self.messages
                    if message.get("receiver_email") == message_room
                ]
                sender_filter = query.get("sender_id", "")
                if sender_filter.startswith("neq."):
                    excluded_sender = sender_filter.removeprefix("neq.")
                    rows = [message for message in rows if message.get("sender_id") != excluded_sender]
                created_after = query.get("created_at", "")
                if created_after.startswith("gt."):
                    cutoff = created_after.removeprefix("gt.")
                    rows = [message for message in rows if message["created_at"] > cutoff]
                cursor = query.get("or", "")
                if cursor:
                    match = re.fullmatch(
                        r"\(created_at\.lt\.(.*?),and\(created_at\.eq\.(.*?),id\.lt\.(.*?)\)\)",
                        cursor,
                    )
                    if match:
                        before_time, _, before_id = match.groups()
                        rows = [
                            message for message in rows
                            if message["created_at"] < before_time
                            or (message["created_at"] == before_time
                                and str(message["id"]) < before_id)
                        ]
                    after_match = re.fullmatch(
                        r"\(created_at\.gt\.(.*?),and\(created_at\.eq\.(.*?),id\.gt\.(.*?)\)\)",
                        cursor,
                    )
                    if after_match:
                        after_time, _, after_id = after_match.groups()
                        rows = [
                            message for message in rows
                            if message["created_at"] > after_time
                            or (message["created_at"] == after_time
                                and str(message["id"]) > after_id)
                        ]
                rows.sort(
                    key=lambda message: (message["created_at"], str(message["id"])),
                    reverse="desc" in query.get("order", "created_at.desc,id.desc"),
                )
                return rows[:int(query.get("limit", server.MAX_MESSAGES))]
            if method == "POST":
                self.saved_message = body
                self.messages.append({
                    **body,
                    "id": len(self.messages) + 1,
                    "created_at": "2026-11-10T12:01:00Z",
                })
                return None
            if method == "DELETE":
                message_id = query.get("id", "").removeprefix("eq.")
                message_room = query.get("receiver_email", "").removeprefix("eq.")
                remaining = []
                deleted = []
                for message in self.messages:
                    matches = (
                        str(message.get("id")) == message_id
                        and message.get("receiver_email") == message_room
                        and message.get("sender_id") == sender_id
                    )
                    (deleted if matches else remaining).append(message)
                self.messages = remaining
                return [{"id": item["id"]} for item in deleted]
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
        status_code, session = self.request(
            "/api/auth/session",
            method="POST",
            payload={"supabaseAccessToken": TEST_SUPABASE_ACCESS_TOKEN},
        )
        self.assertEqual(status_code, 200)
        return session["token"]

    def join_room(self, token, room_code="123456"):
        return self.request(
            "/api/rooms/join",
            method="POST",
            token=token,
            payload={"roomCode": room_code},
        )

    def test_chat_api_rejects_unauthenticated_and_unapproved_origins(self):
        status_code, payload = self.request("/api/messages?room=123456")
        self.assertEqual(status_code, 401)
        self.assertIn("Sign in", payload["error"])

        status_code, _ = self.request(
            "/api/auth/session",
            method="POST",
            payload={"supabaseAccessToken": TEST_SUPABASE_ACCESS_TOKEN},
            origin="https://attacker.example",
        )
        self.assertEqual(status_code, 403)

    def test_github_pages_preflight_allows_bearer_api_requests(self):
        request = Request(
            f"{self.base_url}/api/messages",
            method="OPTIONS",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,apikey",
            },
        )
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
            self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
            self.assertIn("apikey", response.headers["Access-Control-Allow-Headers"])

    def test_invalid_supabase_account_token_is_rejected(self):
        self.auth_user.side_effect = server.ApiError(
            401, "Account sign-in failed. Check your email and password."
        )
        status_code, payload = self.request(
            "/api/auth/session",
            method="POST",
            payload={"supabaseAccessToken": TEST_SUPABASE_ACCESS_TOKEN},
        )
        self.assertEqual(status_code, 401)
        self.assertIn("sign-in failed", payload["error"])

    def test_failed_sign_in_reports_are_limited_and_include_only_reported_email(self):
        results = [
            self.request(
                "/api/security/report-login-failure",
                method="POST",
                payload={"email": " Person@Example.com "},
            )
            for _ in range(server.MAX_SIGN_IN_FAILURE_REPORTS + 1)
        ]

        self.assertTrue(all(status == 202 for status, _ in results))
        self.assertTrue(all(payload["ok"] for _, payload in results))
        self.assertTrue(all(payload["recorded"] for _, payload in results[:-1]))
        self.assertFalse(results[-1][1]["recorded"])
        self.assertEqual(len(self.security_events), 1)
        self.assertEqual(len(self.failed_logins), server.MAX_SIGN_IN_FAILURE_REPORTS + 1)
        event = self.security_events[0]
        self.assertEqual(event["event_type"], "client_reported_sign_in_failure")
        self.assertEqual(event["request_path"], "/api/auth/sign-in")
        self.assertEqual(event["status_code"], 401)
        self.assertEqual(event["account_email"], "person@example.com")
        self.assertIsNone(event["account_id"])
        self.assertEqual(event["client_ip"], "127.0.0.1")
        self.assertNotEqual(event["address_hash"], "127.0.0.1")

    def test_failed_sign_in_report_rejects_password_fields(self):
        status_code, _ = self.request(
            "/api/security/report-login-failure",
            method="POST",
            payload={"email": "user@example.com", "password": "must-not-be-logged"},
        )
        self.assertEqual(status_code, 400)
        self.assertEqual(self.security_events, [])

    def test_invalid_room_code_logs_ip_and_verified_account_identity(self):
        token = self.sign_in()

        status_code, _ = self.join_room(token, "999999")

        self.assertEqual(status_code, 404)
        event = next(
            item for item in self.security_events
            if item["event_type"] == "invalid_room_code"
        )
        self.assertEqual(event["request_path"], "/api/rooms/join")
        self.assertEqual(event["client_ip"], "127.0.0.1")
        self.assertEqual(event["account_id"], TEST_SENDER_ID)
        self.assertEqual(event["account_email"], "user@example.com")
        self.assertNotEqual(event["address_hash"], "127.0.0.1")

    def test_security_activity_is_admin_only_and_masks_address_hash(self):
        non_admin_token = self.sign_in()
        status_code, _ = self.request(
            "/api/admin/security-events",
            token=non_admin_token,
        )
        self.assertEqual(status_code, 403)

        self.auth_user.return_value = {
            "id": TEST_SENDER_ID,
            "email": "admin@example.com",
        }
        admin_token = self.sign_in()
        self.security_events.clear()
        self.security_events.append({
            "event_id": "event-1",
            "created_at": "2026-11-12T12:00:00Z",
            "event_type": "forbidden_request",
            "request_method": "POST",
            "request_path": "/api/admin/accounts",
            "status_code": 403,
            "address_hash": "a" * 64,
            "client_ip": "192.0.2.7",
            "account_id": TEST_SENDER_ID,
            "account_email": "admin@example.com",
        })

        status_code, payload = self.request(
            "/api/admin/security-events",
            token=admin_token,
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["type"], "forbidden_request")
        self.assertEqual(payload["events"][0]["sourceHash"], "a" * 12)
        self.assertEqual(payload["events"][0]["clientIp"], "192.0.2.7")
        self.assertEqual(payload["events"][0]["accountId"], TEST_SENDER_ID)
        self.assertEqual(payload["events"][0]["accountEmail"], "admin@example.com")
        self.assertNotIn("address_hash", payload["events"][0])

    def test_security_event_can_be_deleted_individually_only_by_admin(self):
        self.security_events.append({
            "event_id": "event-1",
            "created_at": "2026-11-12T12:00:00Z",
            "event_type": "forbidden_request",
            "request_method": "POST",
            "request_path": "/api/admin/accounts",
            "status_code": 403,
            "address_hash": "a" * 64,
        })
        non_admin_token = self.sign_in()
        status_code, _ = self.request(
            "/api/admin/security-events/delete",
            method="POST",
            token=non_admin_token,
            payload={"eventId": "event-1"},
        )
        self.assertEqual(status_code, 403)
        self.assertIn("event-1", [
            row["event_id"] for row in self.security_events if "event_id" in row
        ])

        self.auth_user.return_value = {
            "id": TEST_SENDER_ID,
            "email": "admin@example.com",
        }
        admin_token = self.sign_in()
        status_code, response = self.request(
            "/api/admin/security-events/delete",
            method="POST",
            token=admin_token,
            payload={"eventId": "event-1"},
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(response["ok"])
        self.assertNotIn("event-1", [
            row["event_id"] for row in self.security_events if "event_id" in row
        ])

    def test_unread_count_is_room_scoped_and_excludes_my_messages(self):
        token = self.sign_in()
        self.join_room(token)
        status_code, baseline = self.request(
            "/api/messages/unread?room=123456",
            token=token,
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(baseline["unreadCount"], 0)
        cursor = baseline["latestMessage"]
        self.assertNotIn("encrypted_text", cursor)
        self.assertNotIn("sender_id", cursor)
        self.messages.extend([
            {
                "id": 2,
                "created_at": "2026-11-10T12:01:00Z",
                "sender_email": "Other",
                "receiver_email": "123456",
                "sender_id": "another-account",
                "encrypted_text": "PM3." + "B" * 45,
            },
            {
                "id": 3,
                "created_at": "2026-11-10T12:02:00Z",
                "sender_email": "Test",
                "receiver_email": "123456",
                "sender_id": TEST_SENDER_ID,
                "encrypted_text": "PM3." + "C" * 45,
            },
            {
                "id": 4,
                "created_at": "2026-11-10T12:03:00Z",
                "sender_email": "Other",
                "receiver_email": "123456",
                "sender_id": "another-account",
                "encrypted_text": "PM3." + "D" * 45,
            },
        ])

        status_code, unread = self.request(
            "/api/messages/unread?room=123456"
            f"&afterCreatedAt={cursor['created_at']}&afterId={cursor['id']}",
            token=token,
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(unread["unreadCount"], 2)
        self.assertNotIn("messages", unread)
        self.assertNotIn("encrypted_text", unread)

    def test_unread_count_requires_room_membership(self):
        token = self.sign_in()
        status_code, _ = self.request(
            "/api/messages/unread?room=123456",
            token=token,
        )
        self.assertEqual(status_code, 403)

    def test_first_message_in_empty_room_becomes_unread_after_baseline(self):
        self.messages = []
        token = self.sign_in()
        self.join_room(token)
        status_code, baseline = self.request(
            "/api/messages/unread?room=123456",
            token=token,
        )
        self.assertEqual(status_code, 200)
        self.assertIsNone(baseline["latestMessage"])
        self.messages.append({
            "id": 1,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sender_email": "Other",
            "receiver_email": "123456",
            "sender_id": "another-account",
            "encrypted_text": "PM3." + "E" * 45,
        })
        status_code, unread = self.request(
            "/api/messages/unread?room=123456"
            f"&afterCreatedAt={baseline['baselineAt']}",
            token=token,
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(unread["unreadCount"], 1)

    def test_anonymous_supabase_user_cannot_create_an_app_session(self):
        self.auth_user_patch.stop()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "id": TEST_SENDER_ID,
            "is_anonymous": True,
        }).encode()
        with patch.object(server, "urlopen", return_value=response):
            with self.assertRaises(server.ApiError) as raised:
                server._supabase_auth_user(TEST_SUPABASE_ACCESS_TOKEN)
        self.assertEqual(raised.exception.status, 401)

    def test_expired_session_is_rejected(self):
        token = self.sign_in()
        with server._lock:
            server._sessions[server._session_key(token)]["expires"] = time.time() - 1
        status_code, payload = self.request("/api/messages?room=123456", token=token)
        self.assertEqual(status_code, 401)
        self.assertIn("expired", payload["error"])

    def test_sign_in_uses_the_authenticated_supabase_account_id(self):
        first_token = self.sign_in()
        second_token = self.sign_in()

        with server._lock:
            first_sender = server._sessions[server._session_key(first_token)]["sender_id"]
            second_sender = server._sessions[server._session_key(second_token)]["sender_id"]
        self.assertNotEqual(first_token, second_token)
        self.assertEqual(first_sender, TEST_SENDER_ID)
        self.assertEqual(second_sender, TEST_SENDER_ID)

    def test_only_allowlisted_email_gets_admin_session(self):
        self.auth_user.return_value = {
            "id": TEST_SENDER_ID,
            "email": "ADMIN@example.com",
            "app_metadata": {},
        }

        status_code, result = self.request(
            "/api/auth/session",
            method="POST",
            payload={"supabaseAccessToken": TEST_SUPABASE_ACCESS_TOKEN},
        )

        self.assertEqual(status_code, 200)
        self.assertTrue(result["isAdmin"])

    def test_room_code_guessing_is_rate_limited(self):
        token = self.sign_in()
        for attempt in range(server.MAX_ROOM_JOIN_FAILURES):
            status_code, _ = self.join_room(token, room_code=f"{900000 + attempt:06d}")
            self.assertEqual(status_code, 404)

        status_code, payload = self.join_room(token, room_code="999999")
        self.assertEqual(status_code, 429)
        self.assertIn("Too many incorrect", payload["error"])

    def test_message_history_uses_stable_pages(self):
        token = self.sign_in()
        self.join_room(token)
        self.messages = [
            {
                "id": index,
                "created_at": f"2026-11-10T12:{index // 60:02d}:{index % 60:02d}Z",
                "sender_email": "Test",
                "receiver_email": "123456",
                "sender_id": TEST_SENDER_ID,
                "encrypted_text": "PM3." + "A" * 45,
            }
            for index in range(server.MAX_MESSAGES + 1)
        ]

        status_code, newest_page = self.request("/api/messages?room=123456", token=token)
        self.assertEqual(status_code, 200)
        self.assertEqual(len(newest_page["messages"]), server.MAX_MESSAGES)
        self.assertTrue(newest_page["hasMore"])

        oldest = newest_page["messages"][0]
        cursor_path = (
            f"/api/messages?room=123456&beforeCreatedAt={oldest['created_at']}"
            f"&beforeId={oldest['id']}"
        )
        status_code, older_page = self.request(cursor_path, token=token)
        self.assertEqual(status_code, 200)
        self.assertFalse(older_page["hasMore"])
        self.assertEqual(len(older_page["messages"]), 1)
        self.assertLess(older_page["messages"][0]["created_at"], oldest["created_at"])

    def test_authenticated_chat_read_and_write_use_server_identity(self):
        token = self.sign_in()
        status_code, room = self.join_room(token)
        self.assertEqual(status_code, 200)
        self.assertEqual(room["roomName"], "Room 123456")
        status_code, response = self.request("/api/messages?room=123456", token=token)
        self.assertEqual(status_code, 200)
        self.assertEqual(response["messages"][0]["sender_email"], "Test")
        self.assertTrue(response["messages"][0]["can_delete"])
        self.assertEqual(response["roomName"], "Room 123456")

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

    def test_sender_can_delete_their_message_for_the_room(self):
        token = self.sign_in()
        self.join_room(token)

        status_code, payload = self.request(
            "/api/messages/delete",
            method="POST",
            token=token,
            payload={"roomCode": "123456", "messageId": 1},
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.messages, [])

        status_code, response = self.request("/api/messages?room=123456", token=token)
        self.assertEqual(status_code, 200)
        self.assertEqual(response["messages"], [])

    def test_sender_cannot_delete_another_members_message(self):
        token = self.sign_in()
        self.join_room(token)
        self.messages[0]["sender_id"] = "another-user"

        status_code, payload = self.request(
            "/api/messages/delete",
            method="POST",
            token=token,
            payload={"roomCode": "123456", "messageId": 1},
        )
        self.assertEqual(status_code, 404)
        self.assertEqual(len(self.messages), 1)

    def test_message_delete_requires_room_membership(self):
        token = self.sign_in()
        status_code, payload = self.request(
            "/api/messages/delete",
            method="POST",
            token=token,
            payload={"roomCode": "123456", "messageId": 1},
        )
        self.assertEqual(status_code, 403)
        self.assertEqual(len(self.messages), 1)

    def test_message_delete_rejects_invalid_id(self):
        token = self.sign_in()
        self.join_room(token)
        status_code, payload = self.request(
            "/api/messages/delete",
            method="POST",
            token=token,
            payload={"roomCode": "123456", "messageId": "1,or=gt.0"},
        )
        self.assertEqual(status_code, 400)
        self.assertEqual(len(self.messages), 1)

    def test_server_rejects_unencrypted_or_invalid_room_messages(self):
        token = self.sign_in()
        self.join_room(token)
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

    def test_chat_rooms_can_be_named_and_renamed(self):
        token = self.sign_in()
        status_code, created = self.request(
            "/api/rooms",
            method="POST",
            token=token,
            payload={"roomName": "Study group"},
        )
        self.assertEqual(status_code, 201)
        self.assertEqual(created["roomName"], "Study group")
        self.assertRegex(created["roomCode"], r"^[0-9]{6}$")

        status_code, renamed = self.request(
            "/api/rooms/rename",
            method="POST",
            token=token,
            payload={"roomCode": created["roomCode"], "roomName": "Science club"},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(renamed["roomName"], "Science club")

        status_code, joined = self.join_room(token, created["roomCode"])
        self.assertEqual(status_code, 200)
        self.assertEqual(joined["roomName"], "Science club")
        status_code, chat = self.request(
            f"/api/messages?room={created['roomCode']}",
            token=token,
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(chat["roomName"], "Science club")

    def test_signed_in_user_can_list_all_their_chats(self):
        token = self.sign_in()
        status_code, created = self.request(
            "/api/rooms",
            method="POST",
            token=token,
            payload={"roomName": "Study group"},
        )
        self.assertEqual(status_code, 201)
        joined_status, _ = self.join_room(token, "123456")
        self.assertEqual(joined_status, 200)

        status_code, result = self.request("/api/rooms", token=token)

        self.assertEqual(status_code, 200)
        self.assertEqual(
            {(room["roomCode"], room["roomName"]) for room in result["rooms"]},
            {(created["roomCode"], "Study group"), ("123456", "Room 123456")},
        )

    def test_only_admin_can_create_confirmed_account(self):
        token = self.sign_in()
        with patch.object(server, "_supabase_auth_user_details", return_value={
            "id": TEST_SENDER_ID,
            "email": "admin@example.com",
        }), patch.object(server, "_supabase_request", return_value={"id": "new-user-id"}) as create_user:
            status_code, result = self.request(
                "/api/admin/accounts",
                method="POST",
                token=token,
                payload={
                    "supabaseAccessToken": TEST_SUPABASE_ACCESS_TOKEN,
                    "displayName": "New friend",
                    "email": "friend@example.com",
                    "password": "a-strong-test-password",
                },
            )

        self.assertEqual(status_code, 201)
        self.assertEqual(result, {
            "ok": True,
            "email": "friend@example.com",
            "displayName": "New friend",
        })
        call = create_user.call_args
        self.assertEqual(call.args[0], "/auth/v1/admin/users")
        self.assertEqual(call.kwargs["body"]["email_confirm"], True)
        self.assertEqual(call.kwargs["body"]["user_metadata"], {"display_name": "New friend"})

    def test_non_admin_cannot_create_accounts(self):
        token = self.sign_in()
        with patch.object(server, "_supabase_auth_user_details", return_value={
            "id": TEST_SENDER_ID,
            "email": "not-admin@example.com",
        }), patch.object(server, "_supabase_request", side_effect=self.fake_supabase) as create_user:
            status_code, payload = self.request(
                "/api/admin/accounts",
                method="POST",
                token=token,
                payload={
                    "supabaseAccessToken": TEST_SUPABASE_ACCESS_TOKEN,
                    "displayName": "New friend",
                    "email": "friend@example.com",
                    "password": "a-strong-test-password",
                },
            )

        self.assertEqual(status_code, 403)
        self.assertIn("administrator", payload["error"])
        self.assertFalse(any(
            call.args[0] == "/auth/v1/admin/users"
            for call in create_user.call_args_list
        ))

    def test_room_membership_is_required_for_messages_and_renaming(self):
        token = self.sign_in()
        self.join_room(token, "123456")

        status_code, payload = self.request(
            "/api/messages?room=654321",
            token=token,
        )
        self.assertEqual(status_code, 403)
        self.assertIn("Join this chat", payload["error"])

    def test_unknown_room_code_cannot_be_joined(self):
        token = self.sign_in()
        status_code, payload = self.join_room(token, "654321")
        self.assertEqual(status_code, 404)
        self.assertIn("Chat not found", payload["error"])

        status_code, payload = self.request(
            "/api/messages",
            method="POST",
            token=token,
            payload={
                "sender_email": "Test",
                "receiver_email": "654321",
                "encrypted_text": "PM3." + "B" * 45,
            },
        )
        self.assertEqual(status_code, 403)
        self.assertIn("Join this chat", payload["error"])

        status_code, payload = self.request(
            "/api/rooms/rename",
            method="POST",
            token=token,
            payload={"roomCode": "654321", "roomName": "Other room"},
        )
        self.assertEqual(status_code, 403)
        self.assertIn("Join this chat", payload["error"])


if __name__ == "__main__":
    unittest.main()
