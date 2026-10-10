"""Industry Watch Nordics portal.

Serves the dashboard and keeps its data current through two update paths:

1. GitHub sync: a background worker polls a private data repo (written by the
   scheduled task through the GitHub connector), validates and swaps the data.
2. Ingest API: POST /api/ingest with a bearer token.

Both paths run the same validator and keep timestamped history copies.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hmac
import json
import logging
import os
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from auth import COOKIE, SESSION_TTL, AuthStore, PinError

from validator import ValidationError, parse_text, to_js, validate_and_prune

log = logging.getLogger("iw")
logging.basicConfig(level=os.getenv("IW_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

# ---------------------------------------------------------------- config
DATA_DIR = Path(os.getenv("IW_DATA_DIR", "/data"))
STATIC_DIR = Path(__file__).parent / "static"
CURRENT = DATA_DIR / "current.json"
HISTORY = DATA_DIR / "history"
HISTORY_KEEP = int(os.getenv("IW_HISTORY_KEEP", "60"))
MAX_BODY = int(os.getenv("IW_MAX_BODY_BYTES", str(2 * 1024 * 1024)))

INGEST_TOKEN = os.getenv("IW_INGEST_TOKEN", "")
GH_REPO = os.getenv("IW_GITHUB_REPO", "")            # e.g. owner/industry-watch-nordics-data
GH_PATH = os.getenv("IW_GITHUB_PATH", "industry-watch-data.js")
GH_BRANCH = os.getenv("IW_GITHUB_BRANCH", "main")
GH_TOKEN = os.getenv("IW_GITHUB_TOKEN", "")          # fine-grained PAT, Contents: read-only
SYNC_INTERVAL = max(60, int(os.getenv("IW_SYNC_INTERVAL", "300")))
# Alternative to GitHub: any HTTPS URL that returns the data file (e.g. a view-only share link).
SOURCE_URL = os.getenv("IW_SOURCE_URL", "")
if SOURCE_URL and not SOURCE_URL.startswith("https://"):
    raise SystemExit("IW_SOURCE_URL must be an https:// URL")
SYNC_ENABLED = bool(GH_REPO or SOURCE_URL)

if INGEST_TOKEN and len(INGEST_TOKEN) < 32:
    raise SystemExit("IW_INGEST_TOKEN must be at least 32 characters (use: openssl rand -hex 32)")

EMPTY = {
    "updated": "1970-01-01T00:00:00+00:00", "updated_label": "no data yet",
    "briefing": ["No data has been received yet. The portal fills in after the next scheduled refresh or GitHub sync."],
    "items": [], "matrices": [], "watchlist": [],
}

state = {"last_sync": None, "last_sync_result": "not run", "gh_etag": None, "last_ingest": None}
_lock = asyncio.Lock()


# ---------------------------------------------------------------- storage
def load_current() -> dict:
    try:
        return json.loads(CURRENT.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return EMPTY
    except (OSError, json.JSONDecodeError):
        log.exception("current.json unreadable, serving empty data")
        return EMPTY


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def _prune_history() -> None:
    files = sorted(HISTORY.glob("*.json"))
    for old in files[:-HISTORY_KEEP]:
        with contextlib.suppress(OSError):
            old.unlink()


async def apply_update(raw: str, origin: str, allow_older: bool = False) -> dict:
    """Validate raw text and make it the current data. Returns a result dict."""
    data = validate_and_prune(parse_text(raw))
    async with _lock:
        cur = load_current()
        new_ts = dt.datetime.fromisoformat(data["updated"])
        cur_ts = dt.datetime.fromisoformat(cur["updated"])
        if not allow_older and new_ts < cur_ts:
            return {"applied": False, "reason": "older than current data",
                    "current_updated": cur["updated"], "received_updated": data["updated"]}
        if data == cur:
            return {"applied": False, "reason": "identical to current data"}
        text = json.dumps(data, ensure_ascii=False, indent=1)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        _atomic_write(HISTORY / f"{stamp}-{origin}.json", text)
        _atomic_write(CURRENT, text)
        _prune_history()
    log.info("data updated from %s: %d items, updated %s", origin, len(data["items"]), data["updated"])
    return {"applied": True, "items": len(data["items"]), "updated": data["updated"]}


# ---------------------------------------------------------------- GitHub sync
async def github_sync_once(client: httpx.AsyncClient) -> str:
    """Pull the data file once, from IW_SOURCE_URL if set, else from the GitHub repo."""
    headers = {"User-Agent": "industry-watch-nordics"}
    if SOURCE_URL:
        url, params = SOURCE_URL, None
    else:
        url, params = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}", {"ref": GH_BRANCH}
        headers.update({"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28"})
        if GH_TOKEN:
            headers["Authorization"] = f"Bearer {GH_TOKEN}"
    if state["gh_etag"]:
        headers["If-None-Match"] = state["gh_etag"]
    r = await client.get(url, params=params, headers=headers, follow_redirects=True)
    if r.status_code == 304:
        return "unchanged"
    if r.status_code != 200:
        return f"source returned HTTP {r.status_code}"
    if len(r.content) > MAX_BODY:
        return "file too large"
    try:
        res = await apply_update(r.text, "github")
    except ValidationError as exc:
        return f"validation failed: {exc}"
    state["gh_etag"] = r.headers.get("ETag")
    return "applied" if res["applied"] else f"skipped ({res['reason']})"


async def github_sync_loop() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                result = await github_sync_once(client)
            except Exception as exc:  # network errors must not kill the loop
                result = f"error: {type(exc).__name__}"
                log.warning("sync failed: %s", exc)
            state["last_sync"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            state["last_sync_result"] = result
            if result != "unchanged":
                log.info("sync: %s", result)
            await asyncio.sleep(SYNC_INTERVAL)


# ---------------------------------------------------------------- auth / rate limit
_fails: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def require_token(request: Request) -> None:
    if not INGEST_TOKEN:
        raise HTTPException(503, "ingest disabled (IW_INGEST_TOKEN not set)")
    ip, now = client_ip(request), time.monotonic()
    q = _fails[ip]
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, "too many failed attempts")
    auth = request.headers.get("authorization", "")
    supplied = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), INGEST_TOKEN.encode()):
        q.append(now)
        log.warning("rejected ingest auth from %s", ip)
        raise HTTPException(401, "invalid token")


# ---------------------------------------------------------------- app
SECURE_COOKIES = os.getenv("IW_INSECURE_COOKIES") != "1"   # set to 1 only for plain-http LAN testing
PUBLIC_HOST = os.getenv("IW_PUBLIC_HOST", "")                # e.g. industrywatchnordics.gronnevik.org
AUTH: AuthStore | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global AUTH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUTH = AuthStore(DATA_DIR / "auth.json")
    task = asyncio.create_task(github_sync_loop()) if SYNC_ENABLED else None
    src = "source URL" if SOURCE_URL else f"{GH_REPO}:{GH_BRANCH}/{GH_PATH}"
    log.info("started; sync %s; ingest %s",
             f"on ({src}, every {SYNC_INTERVAL}s)" if task else "off",
             "on" if INGEST_TOKEN else "off")
    yield
    if task:
        task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

# Paths reachable without a PIN session. /api/ingest and /api/sync use the bearer token instead.
PUBLIC = {"/login", "/login.js", "/healthz", "/api/login", "/api/logout", "/api/ingest", "/api/sync"}


def role_of(request: Request) -> str | None:
    return AUTH.session_role(request.cookies.get(COOKIE)) if AUTH else None


@app.middleware("http")
async def gate_and_headers(request: Request, call_next):
    path = request.url.path
    if path not in PUBLIC:
        role = role_of(request)
        needs_admin = path in ("/admin", "/admin.js") or path.startswith("/api/admin/")
        if role is None or (needs_admin and role != "admin"):
            if request.method == "GET" and not path.startswith("/api/") and not path.endswith(".js"):
                nxt = "?next=admin" if needs_admin else ""
                resp = RedirectResponse(f"/login{nxt}", status_code=303)
            else:
                resp = JSONResponse({"detail": "PIN required" if role is None else "admin PIN required"},
                                    status_code=401 if role is None else 403)
        else:
            request.state.role = role
            resp = await call_next(request)
    else:
        resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def same_origin(request: Request) -> None:
    """CSRF guard for cookie-authenticated POSTs (on top of SameSite=Strict)."""
    origin = request.headers.get("origin")
    allowed = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", ""), PUBLIC_HOST} - {""}
    if origin is None or origin.split("://", 1)[-1] not in allowed:
        raise HTTPException(403, "cross-origin request refused")


def static(name: str, media: str | None = None):
    return FileResponse(STATIC_DIR / name, media_type=media, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
@app.get("/industry-watch.html", include_in_schema=False)
async def index():
    return static("index.html")


@app.get("/login", include_in_schema=False)
async def login_page(request: Request):
    if role_of(request) and request.query_params.get("next") != "admin":
        return RedirectResponse("/", status_code=303)
    return static("login.html")


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return static("admin.html")


@app.get("/app.js", include_in_schema=False)
async def app_js():
    return static("app.js", "text/javascript")


@app.get("/login.js", include_in_schema=False)
async def login_js():
    return static("login.js", "text/javascript")


@app.get("/admin.js", include_in_schema=False)
async def admin_js():
    return static("admin.js", "text/javascript")


@app.get("/industry-watch-data.js", include_in_schema=False)
async def data_js():
    return Response(to_js(load_current()), media_type="text/javascript", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- session API
async def _read_json(request: Request) -> dict:
    if len(await request.body()) > 4096:
        raise HTTPException(413, "payload too large")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "JSON body required") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON object required")
    return body


def _set_cookie(resp: Response, role: str) -> None:
    resp.set_cookie(COOKIE, AUTH.issue(role), max_age=SESSION_TTL[role], httponly=True,
                    secure=SECURE_COOKIES, samesite="strict", path="/")


@app.post("/api/login")
async def api_login(request: Request):
    same_origin(request)
    ip = client_ip(request)
    if AUTH.locked_out(ip):
        log.warning("login locked out for %s", ip)
        raise HTTPException(429, "Too many attempts. Try again later.")
    pin = str((await _read_json(request)).get("pin", ""))
    role = await run_in_threadpool(AUTH.verify, pin)
    if role is None:
        AUTH.record_fail(ip)
        log.warning("failed PIN attempt from %s", ip)
        await asyncio.sleep(1.0)
        raise HTTPException(401, "Wrong PIN")
    AUTH.clear_fails(ip)
    log.info("%s login from %s", role, ip)
    resp = JSONResponse({"role": role})
    _set_cookie(resp, role)
    return resp


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/", secure=SECURE_COOKIES, httponly=True, samesite="strict")
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    return {"role": request.state.role}


@app.post("/api/admin/pin")
async def api_admin_pin(request: Request):
    """Change a PIN. Body: {target: viewer|admin, current_admin_pin, new_pin}."""
    same_origin(request)
    body = await _read_json(request)
    target = body.get("target")
    if target not in ("viewer", "admin"):
        raise HTTPException(400, "target must be viewer or admin")
    ip = client_ip(request)
    if AUTH.locked_out(ip):
        raise HTTPException(429, "Too many attempts. Try again later.")
    if await run_in_threadpool(AUTH.verify, str(body.get("current_admin_pin", ""))) != "admin":
        AUTH.record_fail(ip)
        raise HTTPException(403, "Current admin PIN is wrong")
    try:
        await run_in_threadpool(AUTH.change, target, str(body.get("new_pin", "")))
    except PinError as exc:
        raise HTTPException(422, str(exc)) from exc
    resp = JSONResponse({"ok": True, "changed": target})
    if target == "admin":  # admin's own session was revoked by the version bump; reissue it
        _set_cookie(resp, "admin")
    return resp


@app.post("/api/admin/revoke")
async def api_admin_revoke(request: Request):
    """Sign out every viewer session without changing the PIN."""
    same_origin(request)
    AUTH.revoke("viewer")
    return {"ok": True}


# ---------------------------------------------------------------- data API
@app.get("/api/data")
async def api_data():
    return JSONResponse(load_current(), headers={"Cache-Control": "no-store"})


@app.get("/api/status")
async def api_status():
    cur = load_current()
    return {"updated": cur["updated"], "updated_label": cur["updated_label"], "items": len(cur["items"]),
            "sync": {"enabled": SYNC_ENABLED, "source": "url" if SOURCE_URL else ("github" if GH_REPO else None),
                     "last_run": state["last_sync"], "last_result": state["last_sync_result"]},
            "ingest_enabled": bool(INGEST_TOKEN), "last_ingest": state["last_ingest"]}


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


@app.post("/api/ingest")
async def api_ingest(request: Request):
    require_token(request)
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "payload too large")
    try:
        res = await apply_update(body.decode("utf-8"), "api",
                                 allow_older=request.query_params.get("force") == "1")
    except (ValidationError, UnicodeDecodeError) as exc:
        raise HTTPException(422, f"validation failed: {exc}") from exc
    state["last_ingest"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return res


@app.post("/api/sync")
async def api_sync(request: Request):
    """Force an immediate pull from the data source (token protected)."""
    require_token(request)
    if not SYNC_ENABLED:
        raise HTTPException(400, "sync not configured (set IW_GITHUB_REPO or IW_SOURCE_URL)")
    state["gh_etag"] = None
    async with httpx.AsyncClient(timeout=30) as client:
        result = await github_sync_once(client)
    state["last_sync"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state["last_sync_result"] = result
    return {"result": result}
