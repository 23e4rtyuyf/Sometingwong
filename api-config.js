// GitHub Pages is static, so it calls the separately hosted Replit API.
// Replit serves the API on the same origin and should use relative requests.
window.CHAT_API_BASE_URL =
  window.location.hostname === "23e4rtyuyf.github.io"
    ? "https://privmessagez.replit.app"
    : "";
