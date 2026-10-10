import importlib, json, os, sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
FIXTURE = (ROOT / "tests/fixtures/sample-data.js").read_text()
TOKEN = "t" * 40
VIEWER, ADMIN = "111222", "33334444"   # test-only PINs
ORIGIN = {"Origin": "http://testserver"}


def _env(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("IW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IW_INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("IW_VIEWER_PIN", VIEWER)
    monkeypatch.setenv("IW_ADMIN_PIN", ADMIN)
    monkeypatch.setenv("IW_INSECURE_COOKIES", "1")
    for k in ("IW_GITHUB_REPO", "IW_SOURCE_URL", "IW_RESET_PINS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    import auth, main
    importlib.reload(auth)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.main = main
        yield c


def login(c, pin):
    return c.post("/api/login", json={"pin": pin}, headers=ORIGIN)


def bearer():
    return {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------- auth
def test_unauthenticated_is_blocked(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/industry-watch-data.js").status_code == 401
    assert client.get("/api/data").status_code == 401
    assert client.get("/api/status").status_code == 401
    assert client.get("/login").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_pins_not_stored_in_clear(client):
    raw = (Path(os.environ["IW_DATA_DIR"]) / "auth.json").read_text()
    assert VIEWER not in raw and ADMIN not in raw


def test_viewer_login_and_access(client):
    assert login(client, "000000").status_code == 401
    r = login(client, VIEWER)
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/").status_code == 200
    assert client.get("/industry-watch-data.js").text.startswith("window.IW_DATA")
    assert client.get("/api/me").json() == {"role": "viewer"}
    # viewers cannot reach admin
    assert client.get("/admin", follow_redirects=False).status_code == 303
    assert client.post("/api/admin/revoke", headers=ORIGIN).status_code == 403
    client.post("/api/logout")
    assert client.get("/api/data").status_code == 401


def test_cross_origin_login_refused(client):
    assert client.post("/api/login", json={"pin": VIEWER}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/login", json={"pin": VIEWER}).status_code == 403


def test_admin_changes_viewer_pin_and_old_sessions_end(client, tmp_path):
    from fastapi.testclient import TestClient
    viewer = TestClient(client.main.app)
    assert login(viewer, VIEWER).status_code == 200
    assert viewer.get("/api/data").status_code == 200

    assert login(client, ADMIN).json()["role"] == "admin"
    assert client.get("/admin").status_code == 200
    bad = client.post("/api/admin/pin", headers=ORIGIN,
                      json={"target": "viewer", "new_pin": "123", "current_admin_pin": ADMIN})
    assert bad.status_code == 422
    wrong = client.post("/api/admin/pin", headers=ORIGIN,
                        json={"target": "viewer", "new_pin": "555666", "current_admin_pin": "99999999"})
    assert wrong.status_code == 403
    same = client.post("/api/admin/pin", headers=ORIGIN,
                       json={"target": "viewer", "new_pin": ADMIN, "current_admin_pin": ADMIN})
    assert same.status_code == 422
    ok = client.post("/api/admin/pin", headers=ORIGIN,
                     json={"target": "viewer", "new_pin": "555666", "current_admin_pin": ADMIN})
    assert ok.status_code == 200
    assert viewer.get("/api/data").status_code == 401          # old session revoked
    assert login(viewer, VIEWER).status_code == 401            # old PIN dead
    assert login(viewer, "555666").status_code == 200          # new PIN works


def test_admin_changes_own_pin_and_keeps_session(client):
    login(client, ADMIN)
    r = client.post("/api/admin/pin", headers=ORIGIN,
                    json={"target": "admin", "new_pin": "77778888", "current_admin_pin": ADMIN})
    assert r.status_code == 200
    assert client.get("/admin").status_code == 200
    client.post("/api/logout")
    assert login(client, ADMIN).status_code == 401
    assert login(client, "77778888").json()["role"] == "admin"


def test_env_ignored_after_first_start(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    import auth
    importlib.reload(auth)
    store = auth.AuthStore(tmp_path / "auth.json")
    store.change("viewer", "999000")
    again = auth.AuthStore(tmp_path / "auth.json")       # restart with old env still set
    assert again.verify("999000") == "viewer" and again.verify(VIEWER) is None
    monkeypatch.setenv("IW_RESET_PINS", "1")
    reset = auth.AuthStore(tmp_path / "auth.json")
    assert reset.verify(VIEWER) == "viewer"


def test_login_lockout(client):
    for _ in range(5):
        assert login(client, "000000").status_code == 401
    assert login(client, VIEWER).status_code == 429


def test_forged_cookie_rejected(client):
    login(client, VIEWER)
    tok = client.cookies.get("iw_session")
    body, sig = tok.rsplit(".", 1)
    import base64
    forged = base64.urlsafe_b64encode(json.dumps({"r": "admin", "v": 1, "e": 9999999999}).encode()).decode().rstrip("=")
    client.cookies.set("iw_session", f"{forged}.{sig}")
    assert client.get("/api/me").status_code == 401


# ---------------------------------------------------------------- data
def test_ingest_requires_token(client):
    assert client.post("/api/ingest", content=FIXTURE).status_code == 401
    assert client.post("/api/ingest", content=FIXTURE, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_ingest_and_serve(client):
    r = client.post("/api/ingest", content=FIXTURE, headers=bearer())
    assert r.status_code == 200 and r.json()["applied"] is True
    login(client, VIEWER)
    assert client.get("/api/status").json()["items"] == 1
    assert "sample-item" in client.get("/industry-watch-data.js").text
    assert client.post("/api/ingest", content=FIXTURE, headers=bearer()).json()["applied"] is False
    assert len(list((Path(os.environ["IW_DATA_DIR"]) / "history").glob("*.json"))) == 1


def test_rejects_invalid_and_older(client):
    bad = FIXTURE.replace('"signal": "Watch"', '"signal": "Maybe"')
    assert client.post("/api/ingest", content=bad, headers=bearer()).status_code == 422
    em = FIXTURE.replace("Sample summary.", "Sample — summary.")
    assert client.post("/api/ingest", content=em.encode(), headers=bearer()).status_code == 422
    client.post("/api/ingest", content=FIXTURE, headers=bearer())
    older = FIXTURE.replace("2026-10-09T06:49:11+02:00", "2026-10-01T06:49:11+02:00")
    assert client.post("/api/ingest", content=older, headers=bearer()).json()["applied"] is False


def test_ingest_rate_limit(client):
    for _ in range(10):
        client.post("/api/ingest", content=FIXTURE, headers={"Authorization": "Bearer x"})
    assert client.post("/api/ingest", content=FIXTURE, headers=bearer()).status_code == 429


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_github_sync(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, IW_GITHUB_REPO="o/r")
    import main
    importlib.reload(main)

    def handler(req: httpx.Request):
        assert req.url.path == "/repos/o/r/contents/industry-watch-data.js"
        if req.headers.get("if-none-match") == '"e1"':
            return httpx.Response(304)
        return httpx.Response(200, text=FIXTURE, headers={"ETag": '"e1"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await main.github_sync_once(c) == "applied"
        assert await main.github_sync_once(c) == "unchanged"
    assert json.loads((tmp_path / "current.json").read_text())["items"][0]["id"] == "sample-item"


@pytest.mark.anyio
async def test_url_source_sync(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, IW_SOURCE_URL="https://files.example/share/abc?download=1")
    import main
    importlib.reload(main)

    def handler(req: httpx.Request):
        assert req.url.host == "files.example"
        return httpx.Response(200, text=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await main.github_sync_once(c) == "applied"
        assert await main.github_sync_once(c) == "skipped (identical to current data)"


def test_cli_validator(tmp_path):
    import validator
    p = tmp_path / "d.js"
    p.write_text(FIXTURE)
    assert validator.main(["x", str(p)]) == 0
