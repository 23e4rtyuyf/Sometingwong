// GitHub Pages is static, so it calls the Supabase Edge Function.
// Replit serves the API on the same origin and should use relative requests.
// Set CHAT_API_KEY only to a public Supabase publishable key, never a service key.
window.CHAT_API_BASE_URL =
  window.location.hostname === "23e4rtyuyf.github.io"
    ? "https://iozdwksqfhlhcrbhrwfl.supabase.co/functions/v1/swift-endpoint"
    : "";
window.CHAT_API_KEY =
  window.location.hostname === "23e4rtyuyf.github.io"
    ? "sb_publishable_LtMNYpBNgyymafFWbaRacg_6_vIgkcM"
    : "";
