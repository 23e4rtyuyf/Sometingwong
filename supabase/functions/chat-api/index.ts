const DEFAULT_SUPABASE_URL = "https://iozdwksqfhlhcrbhrwfl.supabase.co";
const ALLOWED_ORIGIN = "https://23e4rtyuyf.github.io";
const AUTH_SALT = "private-live-chat-auth-v1";
const AUTH_ITERATIONS = 600_000;
const CHALLENGE_TTL_SECONDS = 90;
const SESSION_TTL_SECONDS = 8 * 60 * 60;
const MAX_BODY_BYTES = 32_000;
const MAX_MESSAGES = 500;
const encoder = new TextEncoder();

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function envJsonKey(variable) {
  try {
    const keys = JSON.parse(Deno.env.get(variable) || "{}");
    return typeof keys.default === "string" ? keys.default : "";
  } catch {
    return "";
  }
}

function config() {
  return {
    supabaseUrl: (Deno.env.get("SUPABASE_URL") || DEFAULT_SUPABASE_URL).replace(/\/+$/, ""),
    serviceKey: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || envJsonKey("SUPABASE_SECRET_KEYS"),
    publishableKey: Deno.env.get("SUPABASE_ANON_KEY") || envJsonKey("SUPABASE_PUBLISHABLE_KEYS"),
    password: Deno.env.get("CHAT_ACCESS_PASSWORD") || "",
    sessionSecret: Deno.env.get("SESSION_SECRET") || "",
  };
}

function corsHeaders(request) {
  const headers = new Headers({
    "Access-Control-Allow-Headers": "Authorization, Content-Type, apikey, x-client-info",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Max-Age": "600",
    "Cache-Control": "no-store",
    "Vary": "Origin",
    "X-Content-Type-Options": "nosniff",
  });
  if (request.headers.get("origin") === ALLOWED_ORIGIN) {
    headers.set("Access-Control-Allow-Origin", ALLOWED_ORIGIN);
  }
  return headers;
}

function jsonResponse(request, status, payload) {
  const headers = corsHeaders(request);
  headers.set("Content-Type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(payload), { status, headers });
}

function emptyResponse(request, status = 204) {
  return new Response(null, { status, headers: corsHeaders(request) });
}

function requireOrigin(request) {
  if (request.headers.get("origin") !== ALLOWED_ORIGIN) {
    throw new ApiError(403, "This site is not allowed to use the chat API.");
  }
}

function routeFromUrl(rawUrl) {
  const pathname = new URL(rawUrl).pathname;
  const functionPrefix = "/functions/v1/";
  const prefixIndex = pathname.indexOf(functionPrefix);
  if (prefixIndex >= 0) {
    // Dashboard display names can differ from the deployed function URL slug.
    const functionPath = pathname.slice(prefixIndex + functionPrefix.length);
    const routeIndex = functionPath.indexOf("/");
    return routeIndex >= 0 ? functionPath.slice(routeIndex) || "/" : "/";
  }
  // Some gateways rewrite the forwarded path but leave the function slug in
  // front of the application route, for example /swift-endpoint/api/health.
  const apiIndex = pathname.indexOf("/api/");
  if (apiIndex >= 0) {
    return pathname.slice(apiIndex);
  }
  return pathname;
}

function randomBytes(size) {
  const bytes = new Uint8Array(size);
  crypto.getRandomValues(bytes);
  return bytes;
}

function encodeBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function decodeBase64Url(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new ApiError(400, "Invalid sign-in challenge.");
  }
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function decodeHex(value) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/i.test(value)) return null;
  const bytes = new Uint8Array(value.length / 2);
  for (let index = 0; index < value.length; index += 2) {
    bytes[index / 2] = Number.parseInt(value.slice(index, index + 2), 16);
  }
  return bytes;
}

async function hmac(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(value)));
}

async function hashForStorage(secret, value) {
  return encodeBase64Url(await hmac(secret, value));
}

function clientAddress(request) {
  const forwarded = request.headers.get("x-forwarded-for") || "";
  const firstForwarded = forwarded.split(",")[0].trim();
  return firstForwarded || request.headers.get("cf-connecting-ip") || "unknown";
}

async function readJson(request) {
  const contentLength = Number(request.headers.get("content-length") || 0);
  if (contentLength > MAX_BODY_BYTES) {
    throw new ApiError(413, "Request body is too large.");
  }
  const raw = await request.text();
  if (!raw || encoder.encode(raw).byteLength > MAX_BODY_BYTES) {
    throw new ApiError(413, "Request body is empty or too large.");
  }
  let value;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new ApiError(400, "Invalid JSON request.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(400, "Invalid JSON request.");
  }
  return value;
}

async function supabaseRequest(table, params = {}, options = {}) {
  const settings = config();
  if (!settings.serviceKey) {
    throw new ApiError(503, "Chat storage is not configured on the server.");
  }
  const url = new URL(`${settings.supabaseUrl}/rest/v1/${table}`);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, String(value));
  }
  const headers = new Headers({
    apikey: settings.serviceKey,
    Authorization: `Bearer ${settings.serviceKey}`,
    Accept: "application/json",
  });
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.prefer) headers.set("Prefer", options.prefer);
  let response;
  try {
    response = await fetch(url, {
      method: options.method || "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(502, "Chat storage is temporarily unavailable.");
  }
  const text = await response.text();
  if (!response.ok) {
    console.error(`[chat-api] Supabase request failed with status ${response.status}.`);
    if (response.status === 401 || response.status === 403) {
      throw new ApiError(502, "Chat storage rejected the server configuration.");
    }
    throw new ApiError(502, "Chat storage could not complete the request.");
  }
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError(502, "Chat storage returned an invalid response.");
  }
}

async function cleanupExpiredRows() {
  const now = new Date().toISOString();
  const failedCutoff = new Date(Date.now() - 15 * 60 * 1000).toISOString();
  await Promise.all([
    supabaseRequest("chat_api_challenges", { expires_at: `lt.${now}` }, {
      method: "DELETE",
      prefer: "return=minimal",
    }),
    supabaseRequest("chat_api_sessions", { expires_at: `lt.${now}` }, {
      method: "DELETE",
      prefer: "return=minimal",
    }),
    supabaseRequest("chat_api_failed_logins", { attempted_at: `lt.${failedCutoff}` }, {
      method: "DELETE",
      prefer: "return=minimal",
    }),
  ]);
}

async function issueChallenge(request) {
  const settings = config();
  if (!settings.password || !settings.sessionSecret || !settings.serviceKey) {
    throw new ApiError(503, "Server-side sign-in is not configured.");
  }
  const addressHash = await hashForStorage(settings.sessionSecret, clientAddress(request));
  const now = new Date().toISOString();
  const failed = await supabaseRequest("chat_api_failed_logins", {
    select: "attempt_id",
    address_hash: `eq.${addressHash}`,
    attempted_at: `gt.${new Date(Date.now() - 15 * 60 * 1000).toISOString()}`,
    limit: "8",
  });
  if (Array.isArray(failed) && failed.length >= 8) {
    throw new ApiError(429, "Too many sign-in attempts. Try again later.");
  }
  const open = await supabaseRequest("chat_api_challenges", {
    select: "challenge_id",
    address_hash: `eq.${addressHash}`,
    expires_at: `gt.${now}`,
    limit: "5",
  });
  if (Array.isArray(open) && open.length >= 5) {
    throw new ApiError(429, "Too many open sign-in attempts. Try again shortly.");
  }
  await cleanupExpiredRows();
  const challengeId = encodeBase64Url(randomBytes(24));
  const nonce = encodeBase64Url(randomBytes(32));
  const expiresAt = new Date(Date.now() + CHALLENGE_TTL_SECONDS * 1000).toISOString();
  await supabaseRequest("chat_api_challenges", {}, {
    method: "POST",
    body: {
      challenge_id: challengeId,
      nonce,
      address_hash: addressHash,
      expires_at: expiresAt,
    },
    prefer: "return=minimal",
  });
  return { challengeId, nonce };
}

async function verifyPasswordProof(proof, nonce) {
  const settings = config();
  const proofBytes = decodeHex(proof);
  if (!proofBytes) return false;
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    encoder.encode(settings.password),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  const proofKey = await crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: encoder.encode(AUTH_SALT),
      iterations: AUTH_ITERATIONS,
      hash: "SHA-256",
    },
    keyMaterial,
    { name: "HMAC", hash: "SHA-256", length: 256 },
    false,
    ["verify"],
  );
  return await crypto.subtle.verify("HMAC", proofKey, proofBytes, decodeBase64Url(nonce));
}

async function createAnonymousUser() {
  const settings = config();
  if (!settings.publishableKey) {
    throw new ApiError(503, "Supabase anonymous sign-in is not configured.");
  }
  let response;
  try {
    response = await fetch(`${settings.supabaseUrl}/auth/v1/signup`, {
      method: "POST",
      headers: {
        apikey: settings.publishableKey,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ data: {} }),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(503, "Supabase anonymous sign-in is unavailable.");
  }
  if (!response.ok) {
    console.error(`[chat-api] Supabase anonymous signup failed with status ${response.status}.`);
    throw new ApiError(503, "Supabase anonymous sign-in is unavailable.");
  }
  const payload = await response.json().catch(() => ({}));
  const user = payload?.user || payload;
  const userId = user?.id;
  if (typeof userId !== "string"
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(userId)) {
    throw new ApiError(503, "Supabase anonymous sign-in returned an invalid account.");
  }
  return userId;
}

async function verifyChallenge(request) {
  const settings = config();
  const body = await readJson(request);
  const challengeId = body.challengeId;
  const proof = body.proof;
  if (typeof challengeId !== "string"
    || !/^[A-Za-z0-9_-]{32}$/.test(challengeId)
    || typeof proof !== "string") {
    throw new ApiError(400, "Invalid sign-in proof.");
  }
  const addressHash = await hashForStorage(settings.sessionSecret, clientAddress(request));
  const challenges = await supabaseRequest("chat_api_challenges", {
    select: "challenge_id,nonce,address_hash,expires_at",
    challenge_id: `eq.${challengeId}`,
    address_hash: `eq.${addressHash}`,
    expires_at: `gt.${new Date().toISOString()}`,
  }, {
    method: "DELETE",
    prefer: "return=representation",
  });
  const challenge = Array.isArray(challenges) ? challenges[0] : null;
  if (!challenge) throw new ApiError(401, "Sign-in expired. Try again.");
  if (!settings.password || !settings.sessionSecret) {
    throw new ApiError(503, "Server-side sign-in is not configured.");
  }
  let isValid = false;
  try {
    isValid = await verifyPasswordProof(proof, challenge.nonce);
  } catch {
    isValid = false;
  }
  if (!isValid) {
    await supabaseRequest("chat_api_failed_logins", {}, {
      method: "POST",
      body: {
        attempt_id: encodeBase64Url(randomBytes(18)),
        address_hash: addressHash,
      },
      prefer: "return=minimal",
    });
    throw new ApiError(401, "Incorrect access password.");
  }
  const senderId = await createAnonymousUser();
  const token = encodeBase64Url(randomBytes(32));
  const expiresAt = new Date(Date.now() + SESSION_TTL_SECONDS * 1000).toISOString();
  const tokenHash = await hashForStorage(settings.sessionSecret, token);
  await supabaseRequest("chat_api_sessions", {}, {
    method: "POST",
    body: { token_hash: tokenHash, sender_id: senderId, expires_at: expiresAt },
    prefer: "return=minimal",
  });
  await supabaseRequest("chat_api_failed_logins", {
    address_hash: `eq.${addressHash}`,
  }, { method: "DELETE", prefer: "return=minimal" });
  return { token, expiresAt: Math.floor(Date.parse(expiresAt) / 1000) };
}

async function requireSession(request) {
  const settings = config();
  const authorization = request.headers.get("authorization") || "";
  const match = authorization.match(/^Bearer ([A-Za-z0-9_-]{1,128})$/i);
  if (!match) throw new ApiError(401, "Sign in to continue.");
  const tokenHash = await hashForStorage(settings.sessionSecret, match[1]);
  const rows = await supabaseRequest("chat_api_sessions", {
    select: "sender_id",
    token_hash: `eq.${tokenHash}`,
    expires_at: `gt.${new Date().toISOString()}`,
    limit: "1",
  });
  const session = Array.isArray(rows) ? rows[0] : null;
  if (!session) throw new ApiError(401, "Your session expired. Sign in again.");
  return session;
}

function validatedRoomName(value) {
  const roomName = typeof value === "string" ? value.trim() : "";
  if (!/^[^\x00-\x1f\x7f]{1,40}$/.test(roomName)) {
    throw new ApiError(400, "Chat name must be 1 to 40 characters.");
  }
  return roomName;
}

async function findRoom(roomCode) {
  const rows = await supabaseRequest("chat_api_rooms", {
    select: "room_code,room_name",
    room_code: `eq.${roomCode}`,
    limit: "1",
  });
  return Array.isArray(rows) ? rows[0] || null : null;
}

async function addRoomMember(roomCode, senderId) {
  await supabaseRequest("chat_api_room_members", {
    on_conflict: "room_code,sender_id",
  }, {
    method: "POST",
    body: { room_code: roomCode, sender_id: senderId },
    prefer: "resolution=merge-duplicates,return=minimal",
  });
}

async function requireRoomMembership(roomCode, senderId) {
  const rows = await supabaseRequest("chat_api_room_members", {
    select: "room_code",
    room_code: `eq.${roomCode}`,
    sender_id: `eq.${senderId}`,
    limit: "1",
  });
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new ApiError(403, "Join this chat before opening it.");
  }
}

async function createRoom(request) {
  const session = await requireSession(request);
  const body = await readJson(request);
  const roomName = validatedRoomName(body.roomName);
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const value = new DataView(randomBytes(4).buffer).getUint32(0);
    const roomCode = String(value % 1_000_000).padStart(6, "0");
    const existingRoom = await findRoom(roomCode);
    const existingMessages = await supabaseRequest("messages", {
      select: "id",
      receiver_email: `eq.${roomCode}`,
      limit: "1",
    });
    if (existingRoom || (Array.isArray(existingMessages) && existingMessages.length)) continue;
    await supabaseRequest("chat_api_rooms", {}, {
      method: "POST",
      body: { room_code: roomCode, room_name: roomName },
      prefer: "return=minimal",
    });
    await addRoomMember(roomCode, session.sender_id);
    return { roomCode, roomName };
  }
  throw new ApiError(503, "Could not create a unique chat code. Try again.");
}

async function joinRoom(request) {
  const session = await requireSession(request);
  const body = await readJson(request);
  const roomCode = body.roomCode;
  if (typeof roomCode !== "string" || !/^[0-9]{6}$/.test(roomCode)) {
    throw new ApiError(400, "Enter a valid 6-digit room code.");
  }
  const room = await findRoom(roomCode);
  if (!room) throw new ApiError(404, "Chat not found. Check the room code.");
  await addRoomMember(roomCode, session.sender_id);
  return { roomCode, roomName: room.room_name };
}

async function renameRoom(request) {
  const session = await requireSession(request);
  const body = await readJson(request);
  const roomCode = body.roomCode;
  if (typeof roomCode !== "string" || !/^[0-9]{6}$/.test(roomCode)) {
    throw new ApiError(400, "Enter a valid 6-digit room code.");
  }
  const roomName = validatedRoomName(body.roomName);
  await requireRoomMembership(roomCode, session.sender_id);
  await supabaseRequest("chat_api_rooms", {
    room_code: `eq.${roomCode}`,
  }, {
    method: "PATCH",
    body: { room_name: roomName },
    prefer: "return=minimal",
  });
  return { roomCode, roomName };
}

async function logout(request) {
  const settings = config();
  const authorization = request.headers.get("authorization") || "";
  const match = authorization.match(/^Bearer ([A-Za-z0-9_-]{1,128})$/i);
  if (match && settings.sessionSecret) {
    const tokenHash = await hashForStorage(settings.sessionSecret, match[1]);
    await supabaseRequest("chat_api_sessions", {
      token_hash: `eq.${tokenHash}`,
    }, { method: "DELETE", prefer: "return=minimal" });
  }
  return { ok: true };
}

async function readMessages(request) {
  const session = await requireSession(request);
  const url = new URL(request.url);
  const rooms = url.searchParams.getAll("room");
  const room = rooms.length === 1 ? rooms[0] : "";
  if (!/^[0-9]{6}$/.test(room)) {
    throw new ApiError(400, "Enter a valid 6-digit room code.");
  }
  await requireRoomMembership(room, session.sender_id);
  const roomInfo = await findRoom(room);
  const messages = await supabaseRequest("messages", {
    select: "id,created_at,sender_email,encrypted_text",
    receiver_email: `eq.${room}`,
    order: "created_at.desc",
    limit: String(MAX_MESSAGES),
  });
  if (!Array.isArray(messages)) {
    throw new ApiError(502, "Chat storage returned an invalid response.");
  }
  return {
    messages: messages.reverse(),
    roomName: roomInfo?.room_name || `Room ${room}`,
  };
}

async function insertMessage(request) {
  const session = await requireSession(request);
  const body = await readJson(request);
  const room = body.receiver_email;
  const sender = typeof body.sender_email === "string" ? body.sender_email.trim() : "";
  const ciphertext = body.encrypted_text;
  if (typeof room !== "string" || !/^[0-9]{6}$/.test(room)) {
    throw new ApiError(400, "Enter a valid 6-digit room code.");
  }
  await requireRoomMembership(room, session.sender_id);
  if (!/^[^\x00-\x1f\x7f]{1,40}$/.test(sender)) {
    throw new ApiError(400, "Enter a name between 1 and 40 characters.");
  }
  if (typeof ciphertext !== "string" || !/^PM3\.[A-Za-z0-9_-]{40,24000}$/.test(ciphertext)) {
    throw new ApiError(400, "Message must be encrypted before it is sent.");
  }
  await supabaseRequest("messages", {}, {
    method: "POST",
    body: {
      sender_id: session.sender_id,
      sender_email: sender,
      receiver_email: room,
      encrypted_text: ciphertext,
    },
    prefer: "return=minimal",
  });
  return { ok: true };
}

async function handle(request) {
  const route = routeFromUrl(request.url);
  if (request.method === "OPTIONS") {
    if (request.headers.get("origin") !== ALLOWED_ORIGIN) {
      return jsonResponse(request, 403, { error: "This site is not allowed to use the chat API." });
    }
    return emptyResponse(request);
  }
  if (request.method === "GET" && route === "/api/health") {
    const settings = config();
    return jsonResponse(request, 200, {
      ok: true,
      storageConfigured: Boolean(settings.serviceKey),
      authConfigured: Boolean(settings.password && settings.sessionSecret),
    });
  }
  try {
    requireOrigin(request);
    if (request.method === "GET" && route === "/api/auth/challenge") {
      return jsonResponse(request, 200, await issueChallenge(request));
    }
    if (request.method === "POST" && route === "/api/auth/verify") {
      return jsonResponse(request, 200, await verifyChallenge(request));
    }
    if (request.method === "POST" && route === "/api/auth/logout") {
      return jsonResponse(request, 200, await logout(request));
    }
    if (request.method === "POST" && route === "/api/rooms") {
      return jsonResponse(request, 201, await createRoom(request));
    }
    if (request.method === "POST" && route === "/api/rooms/join") {
      return jsonResponse(request, 200, await joinRoom(request));
    }
    if (request.method === "POST" && route === "/api/rooms/rename") {
      return jsonResponse(request, 200, await renameRoom(request));
    }
    if (request.method === "GET" && route === "/api/messages") {
      return jsonResponse(request, 200, await readMessages(request));
    }
    if (request.method === "POST" && route === "/api/messages") {
      await insertMessage(request);
      return jsonResponse(request, 201, { ok: true });
    }
    throw new ApiError(404, "Not found.");
  } catch (error) {
    if (error instanceof ApiError) {
      return jsonResponse(request, error.status, { error: error.message });
    }
    console.error("[chat-api] Unexpected request failure.");
    return jsonResponse(request, 500, { error: "The chat server could not complete the request." });
  }
}

Deno.serve(handle);
