"""Protection for the web server: headers, body limit, short logs, terms.

Everything here is switched by environment variables, so the same code runs
on a laptop (nothing to configure) and on a server (see DEPLOY notes in the
README):

  TOOL_MAX_BODY_MB      largest request body, default 10 (uploads are
                        also limited to 2 MB per file in server.py)
  TOOL_ACCESS_LOG       short (default): method, path, status, time and the
                        IP with the last part removed; off: nothing
  TOOL_LEGAL_DIR        folder with terms.html, privacy.html, imprint.html.
                        If set, the page links them in the footer and asks
                        each user once to accept the terms.
  TOOL_CONSENT_LOG      where acceptances are recorded (JSON lines: user,
                        terms version, time), default terms_accepted.jsonl
  TOOL_TRUST_REMOTE_USER  1: take the user name from the X-Remote-User header
                        (set by the reverse proxy after its login). Only use
                        this when the app is reachable through the proxy only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from starlette.exceptions import HTTPException

#: The page may only load its own code; map tiles come from OpenStreetMap.
#: style-src needs 'unsafe-inline' for the style attributes Leaflet and the
#: page set on markers; scripts stay strictly 'self'.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https://tile.openstreetmap.org; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    # OpenStreetMap asks for a referrer on tile requests; send only the origin
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

LEGAL_PAGES = ("terms", "privacy", "imprint")

access_logger = logging.getLogger("tour_insertion.access")


# --------------------------------------------------------------------------
# Request body limit (pure ASGI, so it also stops bodies without a length)
# --------------------------------------------------------------------------

class BodyLimit:
    """Answers 413 when a request body is larger than `max_bytes`.

    Checks Content-Length first and also counts the bytes actually received,
    so a client cannot send more than announced."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            return await _too_large(send)
        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge()
            return message

        async def watched_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, watched_send)
        except _TooLarge:
            if not started:
                await _too_large(send)


class _TooLarge(HTTPException):
    """Raised while reading the body; the app's exception handling turns it
    into a 413 answer."""

    def __init__(self):
        super().__init__(413, "request too large")


async def _too_large(send):
    body = b'{"detail":"request too large"}'
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


# --------------------------------------------------------------------------
# Short access log
# --------------------------------------------------------------------------

def short_ip(ip: str | None) -> str:
    """192.168.1.23 -> 192.168.1.0; IPv6: keep the first three groups."""
    if not ip:
        return "-"
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::"
    parts = ip.split(".")
    return ".".join(parts[:3] + ["0"]) if len(parts) == 4 else "-"


def log_request(method: str, path: str, status: int, seconds: float, ip: str | None):
    """One line per request. No query string, no body, no file names."""
    access_logger.info("%s %s %s %d %.0fms", short_ip(ip), method, path, status,
                       seconds * 1000)


# --------------------------------------------------------------------------
# Who is asking (for the record of accepted terms)
# --------------------------------------------------------------------------

def user_of(headers, trust_remote_user: bool) -> str | None:
    """User name from the proxy header (if trusted) or from Basic auth."""
    if trust_remote_user:
        name = (headers.get("x-remote-user") or "").strip()
        if name:
            return name
    auth = headers.get("authorization") or ""
    if auth.startswith("Basic "):
        try:
            name = base64.b64decode(auth[6:]).decode().partition(":")[0].strip()
        except (ValueError, UnicodeDecodeError):
            return None
        return name or None
    return None


# --------------------------------------------------------------------------
# Terms of use: pages, version, acceptances
# --------------------------------------------------------------------------

class Legal:
    """Terms, privacy notice and imprint from a folder of HTML files.

    The terms version is the start of the SHA-256 of terms.html, so every
    change to the text asks all users again. Acceptances are appended to a
    JSON-lines file (user, version, time): proof that the terms were accepted,
    nothing more."""

    def __init__(self, folder: str | Path | None, consent_log: str | Path):
        self.folder = Path(folder) if folder else None
        self.consent_log = Path(consent_log)
        self._lock = threading.Lock()
        self._accepted: set[tuple[str, str]] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.folder and (self.folder / "terms.html").exists())

    def pages(self) -> list[str]:
        if not self.folder:
            return []
        return [p for p in LEGAL_PAGES if (self.folder / f"{p}.html").exists()]

    def page(self, name: str) -> Path | None:
        if name not in LEGAL_PAGES or not self.folder:
            return None
        path = self.folder / f"{name}.html"
        return path if path.exists() else None

    def version(self) -> str:
        if not self.enabled:
            return ""
        return hashlib.sha256((self.folder / "terms.html").read_bytes()).hexdigest()[:12]

    def _load(self) -> set[tuple[str, str]]:
        if self._accepted is None:
            self._accepted = set()
            if self.consent_log.exists():
                for line in self.consent_log.read_text(encoding="utf-8").splitlines():
                    try:
                        entry = json.loads(line)
                        self._accepted.add((entry["user"], entry["version"]))
                    except (ValueError, KeyError, TypeError):
                        continue
        return self._accepted

    def accepted(self, user: str | None) -> bool:
        if not self.enabled:
            return True
        if user is None:            # no login: the browser keeps track
            return False
        with self._lock:
            return (user, self.version()) in self._load()

    def accept(self, user: str | None, version: str) -> bool:
        """Record the acceptance. False if `version` is not the current one."""
        if not self.enabled or version != self.version():
            return False
        if user is None:
            return True
        with self._lock:
            done = self._load()
            if (user, version) not in done:
                self.consent_log.parent.mkdir(parents=True, exist_ok=True)
                entry = {"user": user, "version": version,
                         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                with self.consent_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
                done.add((user, version))
        return True


def now() -> float:
    return time.perf_counter()


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")
