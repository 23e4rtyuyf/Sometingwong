"""Serve the imported site's public files without exposing project internals."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/logo.png": ("logo.png", "image/png"),
    "/logo.svg": ("attached_assets/generated_images/private-messages-mark.svg", "image/svg+xml"),
}


class SiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve_file()

    def do_HEAD(self):
        self.serve_file(head_only=True)

    def serve_file(self, head_only=False):
        resource = PUBLIC_FILES.get(urlsplit(self.path).path)
        if resource is None:
            self.send_error(404)
            return
        filename, content_type = resource
        data = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)


if __name__ == "__main__":
    print("Private Live Chat listening on 0.0.0.0:5000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 5000), SiteHandler).serve_forever()
