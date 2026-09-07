"""
tracking_server.py
====================
5thGenLeadGenerator — Docly open-tracking pixel server.

A tiny HTTP server (stdlib only, no Flask dependency needed) that serves a
1x1 transparent GIF at /open.gif?cid=<contact_id>&step=<step> and records
a docly_tracking_events row every time it's hit.

Runs on its own port (config.TRACKING_PORT, default 8502) as a background
daemon thread for the life of the Streamlit process — same pattern as
docly_scheduler.py.

CRITICAL LIMITATION: this only *works* (i.e. actually receives a hit) if
config.TRACKING_BASE_URL is a PUBLICLY reachable address. A prospect
opening their email is on their own device/network — a bare
"http://localhost:8502" is only reachable from this same laptop and will
never see a real open. Point TRACKING_BASE_URL at an ngrok tunnel or your
VPS's public address for real tracking.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config
import database
from logger_setup import get_logger

logger = get_logger()

# 1x1 transparent GIF, byte-for-byte.
_PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000"
    "01000100000200010000003b"
)

_server_started = False
_server_lock = threading.Lock()


class _TrackingHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout quiet; we log via logger_setup instead

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/open.gif":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        try:
            contact_id = int(params.get("cid", [None])[0])
            step = int(params.get("step", [0])[0])
        except (TypeError, ValueError):
            contact_id, step = None, 0

        if contact_id:
            try:
                database.docly_record_open(
                    contact_id,
                    step,
                    ip_address=self.client_address[0],
                    user_agent=self.headers.get("User-Agent", ""),
                )
                logger.info(f"Docly: open recorded contact_id={contact_id} step={step}")
            except Exception as exc:
                logger.error(f"Docly: failed to record open: {exc}")

        self.send_response(200)
        self.send_header("Content-Type", "image/gif")
        self.send_header("Content-Length", str(len(_PIXEL_GIF)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(_PIXEL_GIF)


def pixel_url(contact_id: int, step: int) -> str | None:
    """Absolute pixel URL to embed in an outgoing email, or None if tracking isn't configured."""
    if not config.docly_tracking_ready():
        return None
    base = config.TRACKING_BASE_URL.rstrip("/")
    return f"{base}/open.gif?cid={contact_id}&step={step}"


def _run():
    server = ThreadingHTTPServer(("0.0.0.0", config.TRACKING_PORT), _TrackingHandler)
    logger.info(f"Docly tracking server listening on 0.0.0.0:{config.TRACKING_PORT}")
    server.serve_forever()


def ensure_started():
    global _server_started
    with _server_lock:
        if _server_started or not config.TRACKING_ENABLED:
            return
        thread = threading.Thread(target=_run, name="docly-tracking-server", daemon=True)
        thread.start()
        _server_started = True
