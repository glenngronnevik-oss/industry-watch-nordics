"""PIN-based access control for the portal.

Two roles:
- viewer: may read the dashboard.
- admin:  may read the dashboard and change PINs on /admin.

PINs are never stored in clear text. On first start the initial PINs are read
from IW_VIEWER_PIN and IW_ADMIN_PIN, hashed with scrypt and written to
/data/auth.json. From then on the environment values are ignored (unless
IW_RESET_PINS=1) and PINs are changed from the admin page.

Sessions are stateless HMAC-signed cookies. Each role has a version number;
changing a PIN bumps the version, which logs out every session of that role.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

log = logging.getLogger("iw.auth")

ROLES = ("viewer", "admin")
MIN_LEN = {"viewer": 6, "admin": 8}
MAX_LEN = 12
SESSION_TTL = {"viewer": int(os.getenv("IW_VIEWER_SESSION_HOURS", "168")) * 3600,
               "admin": int(os.getenv("IW_ADMIN_SESSION_MINUTES", "30")) * 60}
COOKIE = "iw_session"

# Brute-force protection: per client IP, and a global ceiling across all IPs.
IP_MAX_FAILS, IP_WINDOW = 5, 15 * 60
GLOBAL_MAX_FAILS, GLOBAL_WINDOW = 50, 60 * 60

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class PinError(ValueError):
    pass


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, **_SCRYPT)


def check_format(role: str, pin: str) -> None:
    if not isinstance(pin, str) or not pin.isdigit() or not (MIN_LEN[role] <= len(pin) <= MAX_LEN):
        raise PinError(f"{role} PIN must be {MIN_LEN[role]} to {MAX_LEN} digits")


class AuthStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._fails: dict[str, deque] = defaultdict(deque)
        self._global: deque = deque()
        self.data = self._load_or_bootstrap()

    # ------------------------------------------------------------ persistence
    def _load_or_bootstrap(self) -> dict:
        reset = os.getenv("IW_RESET_PINS") == "1"
        if self.path.exists() and not reset:
            return json.loads(self.path.read_text(encoding="utf-8"))
        viewer, admin = os.getenv("IW_VIEWER_PIN", ""), os.getenv("IW_ADMIN_PIN", "")
        if not viewer or not admin:
            raise SystemExit("First start: set IW_VIEWER_PIN and IW_ADMIN_PIN to the initial PINs")
        check_format("viewer", viewer)
        check_format("admin", admin)
        if viewer == admin:
            raise SystemExit("IW_VIEWER_PIN and IW_ADMIN_PIN must differ")
        old = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        data = {"secret": secrets.token_hex(32), "roles": {}}
        for role, pin in (("viewer", viewer), ("admin", admin)):
            ver = old.get("roles", {}).get(role, {}).get("ver", 0) + 1
            data["roles"][role] = self._record(pin, ver)
        self._save(data)
        log.warning("PINs %s from environment; remove IW_VIEWER_PIN/IW_ADMIN_PIN%s from the stack now",
                    "reset" if reset else "initialised", "/IW_RESET_PINS" if reset else "")
        return data

    @staticmethod
    def _record(pin: str, ver: int) -> dict:
        salt = secrets.token_bytes(16)
        return {"salt": salt.hex(), "hash": _hash(pin, salt).hex(), "ver": ver,
                "changed": int(time.time())}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".auth-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)

    # ------------------------------------------------------------ rate limit
    def _prune(self, q: deque, window: int, now: float) -> None:
        while q and now - q[0] > window:
            q.popleft()

    def locked_out(self, ip: str) -> bool:
        now = time.monotonic()
        q = self._fails[ip]
        self._prune(q, IP_WINDOW, now)
        self._prune(self._global, GLOBAL_WINDOW, now)
        return len(q) >= IP_MAX_FAILS or len(self._global) >= GLOBAL_MAX_FAILS

    def record_fail(self, ip: str) -> None:
        now = time.monotonic()
        self._fails[ip].append(now)
        self._global.append(now)

    def clear_fails(self, ip: str) -> None:
        self._fails.pop(ip, None)

    # ------------------------------------------------------------ PINs
    def _matches(self, role: str, pin: str) -> bool:
        rec = self.data["roles"][role]
        return hmac.compare_digest(_hash(pin, bytes.fromhex(rec["salt"])), bytes.fromhex(rec["hash"]))

    def verify(self, pin: str) -> str | None:
        """Return the role the PIN belongs to, or None. Checks both roles so timing is uniform."""
        if not isinstance(pin, str) or not pin.isdigit() or len(pin) > MAX_LEN:
            pin = "0"  # still run both hashes
        admin_ok = self._matches("admin", pin)
        viewer_ok = self._matches("viewer", pin)
        return "admin" if admin_ok else ("viewer" if viewer_ok else None)

    def change(self, role: str, new_pin: str) -> None:
        check_format(role, new_pin)
        other = "admin" if role == "viewer" else "viewer"
        if self._matches(other, new_pin):
            raise PinError("viewer and admin PINs must differ")
        with self._lock:
            data = json.loads(json.dumps(self.data))
            data["roles"][role] = self._record(new_pin, data["roles"][role]["ver"] + 1)
            self._save(data)
            self.data = data
        log.warning("%s PIN changed; all %s sessions signed out", role, role)

    def revoke(self, role: str) -> None:
        with self._lock:
            data = json.loads(json.dumps(self.data))
            data["roles"][role]["ver"] += 1
            self._save(data)
            self.data = data

    # ------------------------------------------------------------ sessions
    def _sign(self, payload: bytes) -> str:
        return hmac.new(bytes.fromhex(self.data["secret"]), payload, hashlib.sha256).hexdigest()

    def issue(self, role: str) -> str:
        body = json.dumps({"r": role, "v": self.data["roles"][role]["ver"],
                           "e": int(time.time()) + SESSION_TTL[role]}, separators=(",", ":")).encode()
        b = base64.urlsafe_b64encode(body).decode().rstrip("=")
        return f"{b}.{self._sign(b.encode())}"

    def session_role(self, token: str | None) -> str | None:
        if not token or "." not in token:
            return None
        b, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(b.encode())):
            return None
        try:
            body = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
        except (ValueError, json.JSONDecodeError):
            return None
        role = body.get("r")
        if role not in ROLES or body.get("e", 0) < time.time():
            return None
        if body.get("v") != self.data["roles"][role]["ver"]:
            return None
        return role
