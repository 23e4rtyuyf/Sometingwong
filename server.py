"""Serve the static chat and proxy authenticated chat operations to Supabase."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
SUPABASE_URL = "https://iozdwksqfhlhcrbhrwfl.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_LtMNYpBNgyymafFWbaRacg_6_vIgkcM"
AUTH_SALT = b"private-live-chat-auth-v1"
AUTH_ITERATIONS = 600_000
CHALLENGE_TTL = 90
SESSION_TTL = 8 * 60 * 60
MAX_BODY_BYTES = 32_000
MAX_MESSAGES = 500
ROOM_JOIN_FAILURE_WINDOW = 15 * 60
MAX_ROOM_JOIN_FAILURES = 8
IDENTITY_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
CURSOR_TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")
ROOM_RE = re.compile(r"^[0-9]{6}$")
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,40}$")
CIPHERTEXT_RE = re.compile(r"^PM3\.[A-Za-z0-9_-]{40,24000}$")
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/api-config.js": ("api-config.js", "application/javascript; charset=utf-8"),
    "/favicon.ico": ("logo.png", "image/png"),
    "/logo.png": ("logo.png", "image/png"),
    "/logo.svg": ("attached_assets/generated_images/private-messages-mark.svg", "image/svg+xml"),
    "/attached_assets/generated_images/private-messages-mark.svg": (
        "attached_assets/generated_images/private-messages-mark.svg",
        "image/svg+xml",
    ),
}

_lock = threading.RLock()
_challenges = {}
_sessions = {}
_identities = {}
_failed_logins = {}
_room_join_failures = {}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _allowed_origins():
    configured = os.environ.get("CHAT_ALLOWED_ORIGINS", "")
    origins = {item.strip().rstrip("/") for item in configured.split(",") if item.strip()}
    origins.add("https://23e4rtyuyf.github.io")
    for key in ("REPLIT_DEV_DOMAIN", "REPLIT_DOMAINS"):
        for domain in os.environ.get(key, "").split(","):
            domain = domain.strip()
            if domain:
                origins.add(domain if "://" in domain else f"https://{domain}")
    return origins


def _encode_b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_json(data):
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError(400, "Invalid JSON request.")
    if not isinstance(value, dict):
        raise ApiError(400, "Invalid JSON request.")
    return value


def _session_key(token):
    secret = os.environ.get("SESSION_SECRET", "").encode("utf-8")
    if not secret:
        raise ApiError(503, "Server-side sign-in is not configured.")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).digest()


def _supabase_request(path, method="GET", body=None, query=None, prefer="return=minimal"):
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not service_key:
        raise ApiError(503, "Chat storage is not configured on the server.")
    url = f"{SUPABASE_URL}{path}"
    if query:
        url += "?" + urlencode(query)
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method=method, headers={
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": prefer,
    })
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as error:
        if error.code in (401, 403):
            if method == "DELETE" and path.endswith("/messages"):
                raise ApiError(502, "Supabase denied message deletion; its existing server permission may not allow DELETE.")
            raise ApiError(502, "Chat storage rejected the server configuration.")
        raise ApiError(502, "Chat storage could not complete the request.")
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise ApiError(502, "Chat storage is temporarily unavailable.")


def _create_anonymous_user():
    request = Request(
        f"{SUPABASE_URL}/auth/v1/signup",
        data=json.dumps({"data": {}}).encode("utf-8"),
        method="POST",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise ApiError(503, "Supabase anonymous sign-in is unavailable.")
    user = payload.get("user") or payload if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        raise ApiError(503, "Supabase anonymous sign-in returned an invalid account.")
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(user_id, str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        user_id,
    ):
        raise ApiError(503, "Supabase anonymous sign-in returned an invalid account.")
    return user_id


class SiteHandler(BaseHTTPRequestHandler):
    server_version = "PrivateChat/2"

    def log_message(self, fmt, *args):
        # Avoid logging IP addresses, request bodies, tokens, or query strings.
        path = urlsplit(self.path).path
        print(f"{self.command} {path}", flush=True)

    def _origin_allowed(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        return bool(origin and origin in _allowed_origins())

    def _cors_headers(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin in _allowed_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")

    def _send_json(self, status, payload):
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(encoded)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "Invalid request size.")
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ApiError(413, "Request body is empty or too large.")
        return _decode_json(self.rfile.read(length))

    def _require_origin(self):
        if not self._origin_allowed():
            raise ApiError(403, "This site is not allowed to use the chat API.")

    def _require_session(self):
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 128:
            raise ApiError(401, "Sign in to continue.")
        key = _session_key(token)
        with _lock:
            session = _sessions.get(key)
            if not session or session["expires"] <= time.time():
                if session:
                    _sessions.pop(key, None)
                raise ApiError(401, "Your session expired. Sign in again.")
            return session

    def do_OPTIONS(self):
        if not self._origin_allowed():
            self._send_json(403, {"error": "This site is not allowed to use the chat API."})
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path in PUBLIC_FILES:
            self._serve_file(path.path)
            return
        if path.path == "/api/health":
            self._send_json(200, {
                "ok": True,
                "storageConfigured": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
                "authConfigured": bool(os.environ.get("CHAT_ACCESS_PASSWORD") and os.environ.get("SESSION_SECRET")),
            })
            return
        try:
            self._require_origin()
            if path.path == "/api/auth/challenge":
                self._issue_challenge()
                return
            if path.path == "/api/messages":
                self._read_messages(parse_qs(path.query))
                return
            raise ApiError(404, "Not found.")
        except ApiError as error:
            self._send_json(error.status, {"error": str(error)})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            self._require_origin()
            if path == "/api/auth/verify":
                self._verify_challenge()
                return
            if path == "/api/auth/logout":
                self._logout()
                return
            if path == "/api/rooms":
                self._create_room()
                return
            if path == "/api/rooms/join":
                self._join_room()
                return
            if path == "/api/rooms/rename":
                self._rename_room()
                return
            if path == "/api/messages":
                self._insert_message()
                return
            if path == "/api/messages/delete":
                self._delete_message()
                return
            raise ApiError(404, "Not found.")
        except ApiError as error:
            self._send_json(error.status, {"error": str(error)})

    def _serve_file(self, path, head_only=False):
        filename, content_type = PUBLIC_FILES[path]
        try:
            data = (ROOT / filename).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path not in PUBLIC_FILES:
            self.send_error(404)
            return
        self._serve_file(path, head_only=True)

    def _issue_challenge(self):
        if not all(os.environ.get(key) for key in (
            "CHAT_ACCESS_PASSWORD", "SESSION_SECRET", "SUPABASE_SERVICE_ROLE_KEY"
        )):
            raise ApiError(503, "Server-side sign-in is not configured.")
        address = self.client_address[0]
        now = time.time()
        with _lock:
            for address, stamps in list(_failed_logins.items()):
                recent = [stamp for stamp in stamps if now - stamp < 900]
                if recent:
                    _failed_logins[address] = recent
                else:
                    _failed_logins.pop(address, None)
            for key, value in list(_challenges.items()):
                if value["expires"] <= now:
                    _challenges.pop(key, None)
            if len(_challenges) >= 2_000:
                raise ApiError(503, "Sign-in is temporarily busy. Try again shortly.")
            attempts = [stamp for stamp in _failed_logins.get(address, []) if now - stamp < 900]
            _failed_logins[address] = attempts
            if len(attempts) >= 8:
                raise ApiError(429, "Too many sign-in attempts. Try again later.")
            if sum(1 for value in _challenges.values() if value["address"] == address) >= 5:
                raise ApiError(429, "Too many open sign-in attempts. Try again shortly.")
            challenge_id = secrets.token_urlsafe(24)
            nonce = secrets.token_bytes(32)
            _challenges[challenge_id] = {
                "nonce": nonce,
                "address": address,
                "expires": now + CHALLENGE_TTL,
            }
        self._send_json(200, {"challengeId": challenge_id, "nonce": _encode_b64(nonce)})

    def _verify_challenge(self):
        request = self._read_body()
        challenge_id = request.get("challengeId")
        proof = request.get("proof")
        identity_token = request.get("identityToken")
        previous_token = request.get("previousToken")
        if not isinstance(challenge_id, str) or not isinstance(proof, str):
            raise ApiError(400, "Invalid sign-in proof.")
        if identity_token is not None and (
            not isinstance(identity_token, str) or not IDENTITY_TOKEN_RE.fullmatch(identity_token)
        ):
            raise ApiError(400, "Invalid local chat identity.")
        with _lock:
            challenge = _challenges.pop(challenge_id, None)
        if not challenge or challenge["expires"] <= time.time() or challenge["address"] != self.client_address[0]:
            raise ApiError(401, "Sign-in expired. Try again.")
        password = os.environ.get("CHAT_ACCESS_PASSWORD", "")
        if not password or not os.environ.get("SESSION_SECRET"):
            raise ApiError(503, "Server-side sign-in is not configured.")
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), AUTH_SALT, AUTH_ITERATIONS)
        expected = hmac.new(key, challenge["nonce"], hashlib.sha256).hexdigest()
        if not hmac.compare_digest(proof, expected):
            with _lock:
                _failed_logins.setdefault(challenge["address"], []).append(time.time())
            raise ApiError(401, "Incorrect access password.")

        sender_id = None
        identity_key = _session_key(f"identity:{identity_token}") if identity_token else None
        with _lock:
            if identity_key:
                sender_id = _identities.get(identity_key)
            if not sender_id and isinstance(previous_token, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", previous_token):
                previous = _sessions.get(_session_key(previous_token))
                if previous:
                    sender_id = previous["sender_id"]
        if not sender_id:
            sender_id = _create_anonymous_user()
        if identity_key:
            with _lock:
                sender_id = _identities.setdefault(identity_key, sender_id)
        token = secrets.token_urlsafe(32)
        session_key = _session_key(token)
        expires = time.time() + SESSION_TTL
        with _lock:
            _sessions[session_key] = {"sender_id": sender_id, "expires": expires}
            _failed_logins.pop(challenge["address"], None)
        self._send_json(200, {"token": token, "expiresAt": int(expires)})

    def _logout(self):
        token = self.headers.get("Authorization", "").partition(" ")[2]
        if token:
            with _lock:
                _sessions.pop(_session_key(token), None)
        self._send_json(200, {"ok": True})

    def _find_room(self, room):
        rows = _supabase_request("/rest/v1/chat_api_rooms", query={
            "select": "room_code,room_name",
            "room_code": f"eq.{room}",
            "limit": "1",
        })
        return rows[0] if isinstance(rows, list) and rows else None

    def _add_room_member(self, room, sender_id):
        _supabase_request(
            "/rest/v1/chat_api_room_members",
            method="POST",
            body={"room_code": room, "sender_id": sender_id},
            query={"on_conflict": "room_code,sender_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def _require_room_membership(self, session, room):
        rows = _supabase_request("/rest/v1/chat_api_room_members", query={
            "select": "room_code",
            "room_code": f"eq.{room}",
            "sender_id": f"eq.{session['sender_id']}",
            "limit": "1",
        })
        if not isinstance(rows, list) or not rows:
            raise ApiError(403, "Join this chat before opening it.")

    def _create_room(self):
        session = self._require_session()
        body = self._read_body()
        room_name = body.get("roomName")
        if not isinstance(room_name, str) or not NAME_RE.fullmatch(room_name.strip()):
            raise ApiError(400, "Chat name must be 1 to 40 characters.")
        room_name = room_name.strip()
        for _ in range(20):
            room = f"{secrets.randbelow(1_000_000):06d}"
            existing = self._find_room(room)
            messages = _supabase_request("/rest/v1/messages", query={
                "select": "id",
                "receiver_email": f"eq.{room}",
                "limit": "1",
            })
            if existing or (isinstance(messages, list) and messages):
                continue
            _supabase_request(
                "/rest/v1/chat_api_rooms",
                method="POST",
                body={"room_code": room, "room_name": room_name},
            )
            self._add_room_member(room, session["sender_id"])
            self._send_json(201, {"roomCode": room, "roomName": room_name})
            return
        raise ApiError(503, "Could not create a unique chat code. Try again.")

    def _join_room(self):
        session = self._require_session()
        body = self._read_body()
        room = body.get("roomCode")
        if not isinstance(room, str) or not ROOM_RE.fullmatch(room):
            raise ApiError(400, "Enter a valid 6-digit room code.")
        now = time.time()
        keys = [
            ("ip", self.client_address[0]),
            ("sender", session["sender_id"]),
        ]
        with _lock:
            for key, stamps in list(_room_join_failures.items()):
                recent = [
                    stamp for stamp in stamps
                    if now - stamp < ROOM_JOIN_FAILURE_WINDOW
                ]
                if recent:
                    _room_join_failures[key] = recent
                else:
                    _room_join_failures.pop(key, None)
            for key in keys:
                attempts = [
                    stamp for stamp in _room_join_failures.get(key, [])
                    if now - stamp < ROOM_JOIN_FAILURE_WINDOW
                ]
                _room_join_failures[key] = attempts
                if len(attempts) >= MAX_ROOM_JOIN_FAILURES:
                    raise ApiError(429, "Too many incorrect chat codes. Try again later.")
        room_info = self._find_room(room)
        if not room_info:
            with _lock:
                for key in keys:
                    _room_join_failures.setdefault(key, []).append(now)
            raise ApiError(404, "Chat not found. Check the room code.")
        self._add_room_member(room, session["sender_id"])
        self._send_json(200, {"roomCode": room, "roomName": room_info["room_name"]})

    def _rename_room(self):
        session = self._require_session()
        body = self._read_body()
        room = body.get("roomCode")
        room_name = body.get("roomName")
        if not isinstance(room, str) or not ROOM_RE.fullmatch(room):
            raise ApiError(400, "Enter a valid 6-digit room code.")
        if not isinstance(room_name, str) or not NAME_RE.fullmatch(room_name.strip()):
            raise ApiError(400, "Chat name must be 1 to 40 characters.")
        self._require_room_membership(session, room)
        room_name = room_name.strip()
        _supabase_request(
            "/rest/v1/chat_api_rooms",
            method="PATCH",
            body={"room_name": room_name},
            query={"room_code": f"eq.{room}"},
        )
        self._send_json(200, {"roomCode": room, "roomName": room_name})

    def _read_messages(self, query):
        session = self._require_session()
        room_values = query.get("room", [])
        room = room_values[0] if len(room_values) == 1 else ""
        if not ROOM_RE.fullmatch(room):
            raise ApiError(400, "Enter a valid 6-digit room code.")
        cursor_times = query.get("beforeCreatedAt", [])
        cursor_ids = query.get("beforeId", [])
        before_created_at = cursor_times[0] if len(cursor_times) == 1 else ""
        before_id = cursor_ids[0] if len(cursor_ids) == 1 else ""
        if bool(before_created_at) != bool(before_id):
            raise ApiError(400, "Invalid older-message cursor.")
        if before_created_at:
            if (len(before_created_at) > 40 or not CURSOR_TIMESTAMP_RE.fullmatch(before_created_at)
                or not MESSAGE_ID_RE.fullmatch(before_id)):
                raise ApiError(400, "Invalid older-message cursor.")
            try:
                datetime.fromisoformat(before_created_at.replace("Z", "+00:00"))
            except ValueError:
                raise ApiError(400, "Invalid older-message cursor.")
        self._require_room_membership(session, room)
        room_info = self._find_room(room)
        message_query = {
            "select": "id,created_at,sender_email,encrypted_text,sender_id",
            "receiver_email": f"eq.{room}",
            "order": "created_at.desc,id.desc",
            "limit": str(MAX_MESSAGES + 1),
        }
        if before_created_at:
            message_query["or"] = (
                f"(created_at.lt.{before_created_at},"
                f"and(created_at.eq.{before_created_at},id.lt.{before_id}))"
            )
        rows = _supabase_request("/rest/v1/messages", query=message_query)
        if not isinstance(rows, list):
            raise ApiError(502, "Chat storage returned an invalid response.")
        has_more = len(rows) > MAX_MESSAGES
        rows = rows[:MAX_MESSAGES]
        messages = [
            {
                **{key: value for key, value in row.items() if key != "sender_id"},
                "can_delete": row.get("sender_id") == session["sender_id"],
            }
            for row in reversed(rows)
        ]
        self._send_json(200, {
            "messages": messages,
            "roomName": room_info["room_name"] if room_info else f"Room {room}",
            "hasMore": has_more,
        })

    def _insert_message(self):
        session = self._require_session()
        body = self._read_body()
        room = body.get("receiver_email")
        sender = body.get("sender_email")
        ciphertext = body.get("encrypted_text")
        if not isinstance(room, str) or not ROOM_RE.fullmatch(room):
            raise ApiError(400, "Enter a valid 6-digit room code.")
        self._require_room_membership(session, room)
        if not isinstance(sender, str) or not NAME_RE.fullmatch(sender.strip()):
            raise ApiError(400, "Enter a name between 1 and 40 characters.")
        if not isinstance(ciphertext, str) or not CIPHERTEXT_RE.fullmatch(ciphertext):
            raise ApiError(400, "Message must be encrypted before it is sent.")
        _supabase_request(
            "/rest/v1/messages",
            method="POST",
            body={
                "sender_id": session["sender_id"],
                "sender_email": sender.strip(),
                "receiver_email": room,
                "encrypted_text": ciphertext,
            },
        )
        self._send_json(201, {"ok": True})

    def _delete_message(self):
        session = self._require_session()
        body = self._read_body()
        room = body.get("roomCode")
        message_id = body.get("messageId")
        if not isinstance(room, str) or not ROOM_RE.fullmatch(room):
            raise ApiError(400, "Enter a valid 6-digit room code.")
        if isinstance(message_id, bool) or not MESSAGE_ID_RE.fullmatch(str(message_id or "")):
            raise ApiError(400, "Invalid message ID.")
        self._require_room_membership(session, room)
        deleted = _supabase_request(
            "/rest/v1/messages",
            method="DELETE",
            query={
                "id": f"eq.{message_id}",
                "receiver_email": f"eq.{room}",
                "sender_id": f"eq.{session['sender_id']}",
                "select": "id",
            },
            prefer="return=representation",
        )
        if not isinstance(deleted, list):
            raise ApiError(502, "Chat storage returned an invalid deletion response.")
        if not deleted:
            raise ApiError(404, "Message not found or you cannot delete it.")
        self._send_json(200, {"ok": True, "messageId": str(message_id)})


if __name__ == "__main__":
    print("Private Live Chat API listening on 0.0.0.0:5000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 5000), SiteHandler).serve_forever()
