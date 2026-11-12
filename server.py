"""Serve the static chat and proxy authenticated chat operations to Supabase."""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
SUPABASE_URL = "https://iozdwksqfhlhcrbhrwfl.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_LtMNYpBNgyymafFWbaRacg_6_vIgkcM"
SESSION_TTL = 8 * 60 * 60
MAX_BODY_BYTES = 32_000
MAX_MESSAGES = 500
ROOM_JOIN_FAILURE_WINDOW = 15 * 60
MAX_ROOM_JOIN_FAILURES = 8
MAX_SIGN_IN_FAILURE_REPORTS = 5
SECURITY_EVENT_RETENTION_DAYS = 30
SECURITY_EVENT_LIST_LIMIT = 100
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
_sessions = {}
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


def _supabase_auth_user_details(access_token):
    request = Request(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code in (400, 401, 403):
            raise ApiError(401, "Account sign-in failed. Check your email and password.")
        raise ApiError(503, "Account sign-in is temporarily unavailable.")
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise ApiError(503, "Account sign-in is temporarily unavailable.")
    user_id = payload.get("id") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or payload.get("is_anonymous") is True
        or not isinstance(user_id, str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        user_id,
    )):
        raise ApiError(401, "Account sign-in failed. Check your email and password.")
    return payload


def _supabase_auth_user(access_token):
    return _supabase_auth_user_details(access_token)["id"]


def _is_chat_admin(user):
    email = user.get("email") if isinstance(user, dict) else None
    if not isinstance(email, str):
        return False
    allowed = {
        value.strip().lower()
        for value in os.environ.get("CHAT_ADMIN_EMAILS", "").split(",")
        if value.strip()
    }
    return email.strip().lower() in allowed


def _security_event_type(path, status):
    if path == "/api/rooms/join" and status == 404:
        return "invalid_room_code"
    if status == 401:
        return "unauthorized_request"
    if status == 403:
        return "forbidden_request"
    if status == 429:
        return "rate_limited_request"
    if status == 400 and path == "/api/admin/accounts":
        return "invalid_admin_request"
    if status == 404:
        return "unknown_route"
    if status >= 500:
        return "server_error"
    return ""


def _security_address_hash(namespace, address):
    secret = os.environ.get("SESSION_SECRET", "").encode("utf-8")
    if not secret or not address:
        return None
    message = f"{namespace}:{address}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _allow_security_event(handler, event_type):
    address = handler.client_address[0] if handler.client_address else ""
    if not address or not os.environ.get("SESSION_SECRET"):
        return False
    address_hash = _security_address_hash(f"security-log:{event_type}", address)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    recent = _supabase_request("/rest/v1/chat_api_failed_logins", query={
        "select": "attempt_id",
        "address_hash": f"eq.{address_hash}",
        "attempted_at": f"gt.{cutoff}",
        "limit": "1",
    })
    if not isinstance(recent, list):
        raise ApiError(502, "Security activity rate limit returned an invalid response.")
    if recent:
        return False
    _supabase_request(
        "/rest/v1/chat_api_failed_logins",
        method="POST",
        body={
            "attempt_id": secrets.token_urlsafe(18),
            "address_hash": address_hash,
        },
    )
    return True


def _write_security_event(handler, event_type, path, status):
    clean_path = re.sub(r"[^A-Za-z0-9/_-]", "", str(path))[:120]
    if not clean_path.startswith("/api/"):
        clean_path = "/api/unknown"
    address = handler.client_address[0] if handler.client_address else ""
    address_hash = _security_address_hash("security-event-ip", address)
    _supabase_request(
        "/rest/v1/chat_api_security_events",
        method="POST",
        body={
            "event_id": secrets.token_urlsafe(18),
            "event_type": event_type,
            "request_method": "GET" if handler.command == "GET" else "POST",
            "request_path": clean_path,
            "status_code": status,
            "address_hash": address_hash,
        },
    )


def _cleanup_security_data():
    now = datetime.now(timezone.utc)
    failed_cutoff = (now - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    event_cutoff = (
        now - timedelta(days=SECURITY_EVENT_RETENTION_DAYS)
    ).isoformat().replace("+00:00", "Z")
    _supabase_request(
        "/rest/v1/chat_api_failed_logins",
        method="DELETE",
        query={"attempted_at": f"lt.{failed_cutoff}"},
    )
    _supabase_request(
        "/rest/v1/chat_api_security_events",
        method="DELETE",
        query={"created_at": f"lt.{event_cutoff}"},
    )


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
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, apikey")
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

    def _require_admin_session(self):
        session = self._require_session()
        if session.get("is_admin") is not True:
            raise ApiError(403, "Only an approved account administrator can view security activity.")
        return session

    def _record_security_failure(self, path, status):
        event_type = _security_event_type(path, status)
        if not event_type:
            return
        try:
            if _allow_security_event(self, event_type):
                _write_security_event(self, event_type, path, status)
        except Exception:
            print("[chat-api] Security event could not be recorded.", flush=True)

    def do_OPTIONS(self):
        if not self._origin_allowed():
            self._record_security_failure(urlsplit(self.path).path, 403)
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
                "authConfigured": bool(SUPABASE_ANON_KEY and os.environ.get("SESSION_SECRET")),
            })
            return
        try:
            self._require_origin()
            if path.path == "/api/rooms":
                self._list_rooms()
                return
            if path.path == "/api/admin/security-events":
                self._list_security_events()
                return
            if path.path == "/api/messages":
                self._read_messages(parse_qs(path.query))
                return
            raise ApiError(404, "Not found.")
        except ApiError as error:
            self._record_security_failure(path.path, error.status)
            self._send_json(error.status, {"error": str(error)})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            self._require_origin()
            if path == "/api/auth/session":
                self._create_api_session()
                return
            if path == "/api/security/report-login-failure":
                self._report_sign_in_failure()
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
            if path == "/api/admin/accounts":
                self._create_account()
                return
            if path == "/api/messages":
                self._insert_message()
                return
            if path == "/api/messages/delete":
                self._delete_message()
                return
            raise ApiError(404, "Not found.")
        except ApiError as error:
            self._record_security_failure(path, error.status)
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

    def _create_api_session(self):
        access_token = self._read_body().get("supabaseAccessToken")
        if not isinstance(access_token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{64,8192}", access_token):
            raise ApiError(401, "Account sign-in failed. Check your email and password.")
        if not os.environ.get("SESSION_SECRET") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
            raise ApiError(503, "Server-side sessions are not configured.")
        user = _supabase_auth_user_details(access_token)
        sender_id = user["id"]
        is_admin = _is_chat_admin(user)
        try:
            _cleanup_security_data()
        except Exception:
            print("[chat-api] Expired security activity could not be cleaned up.", flush=True)
        token = secrets.token_urlsafe(32)
        session_key = _session_key(token)
        expires = time.time() + SESSION_TTL
        with _lock:
            _sessions[session_key] = {
                "sender_id": sender_id,
                "expires": expires,
                "is_admin": is_admin,
            }
        self._send_json(200, {"token": token, "expiresAt": int(expires), "isAdmin": is_admin})

    def _report_sign_in_failure(self):
        body = self._read_body()
        if body:
            raise ApiError(400, "Unexpected sign-in report data.")
        secret = os.environ.get("SESSION_SECRET", "")
        address = self.client_address[0] if self.client_address else ""
        if not secret or not address or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
            self._send_json(202, {"ok": True, "recorded": False})
            return
        address_hash = _security_address_hash("reported-sign-in-ip", address)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=15)
        ).isoformat().replace("+00:00", "Z")
        recent = _supabase_request("/rest/v1/chat_api_failed_logins", query={
            "select": "attempt_id",
            "address_hash": f"eq.{address_hash}",
            "attempted_at": f"gt.{cutoff}",
            "limit": str(MAX_SIGN_IN_FAILURE_REPORTS),
        })
        if not isinstance(recent, list):
            raise ApiError(502, "Security activity storage returned an invalid response.")
        if len(recent) >= MAX_SIGN_IN_FAILURE_REPORTS:
            self._send_json(202, {"ok": True, "recorded": False})
            return
        _supabase_request(
            "/rest/v1/chat_api_failed_logins",
            method="POST",
            body={
                "attempt_id": secrets.token_urlsafe(18),
                "address_hash": address_hash,
            },
        )
        if _allow_security_event(self, "client_reported_sign_in_failure"):
            _write_security_event(
                self,
                "client_reported_sign_in_failure",
                "/api/auth/sign-in",
                401,
            )
        self._send_json(202, {"ok": True, "recorded": True})

    def _list_security_events(self):
        self._require_admin_session()
        rows = _supabase_request("/rest/v1/chat_api_security_events", query={
            "select": "event_id,created_at,event_type,request_method,request_path,status_code,address_hash",
            "order": "created_at.desc",
            "limit": str(SECURITY_EVENT_LIST_LIMIT),
        })
        if not isinstance(rows, list):
            raise ApiError(502, "Security activity storage returned an invalid response.")
        events = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_hash = row.get("address_hash")
            events.append({
                "id": row.get("event_id"),
                "occurredAt": row.get("created_at"),
                "type": row.get("event_type"),
                "method": row.get("request_method"),
                "path": row.get("request_path"),
                "status": row.get("status_code"),
                "sourceHash": source_hash[:12] if isinstance(source_hash, str) else "",
            })
        self._send_json(200, {"events": events})

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

    def _list_rooms(self):
        session = self._require_session()
        memberships = _supabase_request("/rest/v1/chat_api_room_members", query={
            "select": "room_code,joined_at",
            "sender_id": f"eq.{session['sender_id']}",
            "order": "joined_at.desc",
            "limit": "100",
        })
        if not isinstance(memberships, list):
            raise ApiError(502, "Chat storage returned an invalid room list.")
        rooms = []
        for membership in memberships:
            code = membership.get("room_code") if isinstance(membership, dict) else ""
            if not isinstance(code, str) or not ROOM_RE.fullmatch(code):
                continue
            room = self._find_room(code)
            if room:
                rooms.append({"roomCode": code, "roomName": room["room_name"]})
        self._send_json(200, {"rooms": rooms})

    def _create_account(self):
        session = self._require_session()
        body = self._read_body()
        access_token = body.get("supabaseAccessToken")
        if not isinstance(access_token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{64,8192}", access_token):
            raise ApiError(401, "Sign in again before adding an account.")
        user = _supabase_auth_user_details(access_token)
        if user.get("id") != session["sender_id"]:
            raise ApiError(403, "This account cannot manage other accounts.")
        if not _is_chat_admin(user):
            raise ApiError(403, "Only an approved account administrator can add accounts.")
        email = body.get("email")
        if not isinstance(email, str):
            raise ApiError(400, "Enter a valid email address.")
        email = email.strip().lower()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ApiError(400, "Enter a valid email address.")
        password = body.get("password")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ApiError(400, "Use a password between 8 and 128 characters.")
        display_name = body.get("displayName")
        if not isinstance(display_name, str) or not NAME_RE.fullmatch(display_name.strip()):
            raise ApiError(400, "Enter a display name between 1 and 40 characters.")
        created = _supabase_request("/auth/v1/admin/users", method="POST", body={
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"display_name": display_name.strip()},
        })
        if not isinstance(created, dict) or not created.get("id"):
            raise ApiError(502, "Supabase did not confirm the account was created.")
        self._send_json(201, {"ok": True, "email": email, "displayName": display_name.strip()})

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
