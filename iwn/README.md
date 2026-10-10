# Industry Watch Nordics

Self-hosted portal for the Industry Watch dashboard. It runs as a Docker stack on a Raspberry Pi (Portainer), is published at **https://industrywatchnordics.gronnevik.org** through a Cloudflare Tunnel (TLS at Cloudflare's edge, no open ports on your router). It updates itself by polling a data source or accepting authenticated pushes.

```text
 Claude scheduled task (weekdays 06:45 Oslo)
   │ research → validate → upload (route to the Pi: see open item below)
   ▼
 Data source: GitHub data repo, or any https URL (IW_SOURCE_URL)
   ▲ polled every 5 min (read-only)
   │
 Raspberry Pi (Portainer stack "industry-watch")
   ├─ iw-portal       FastAPI: page, data, /api/ingest, /api/sync, pull-sync worker
   └─ iw-cloudflared  outbound tunnel ──► Cloudflare edge (HTTPS) ──► internet
```

## Access control

Everything except `/login`, `/healthz` and the token-protected machine endpoints requires a PIN.

| Role | PIN | Can |
| --- | --- | --- |
| Viewer | 6 to 12 digits | Read the dashboard, `/api/data`, `/api/status` |
| Admin | 8 to 12 digits | Everything a viewer can, plus `/admin`: change the viewer PIN, change the admin PIN, sign out all viewers |

- Initial PINs come from `IW_VIEWER_PIN` and `IW_ADMIN_PIN` on the **first start only**. They are hashed with scrypt into `/data/auth.json` (mode 600), and the environment values are ignored afterwards. Clear them from Portainer once the stack is up.
- Changing a PIN signs out every session of that role. Admins must re-enter the current admin PIN to change either PIN.
- Sessions are HMAC-signed, `HttpOnly`, `Secure`, `SameSite=Strict` cookies: 7 days for viewers, 30 minutes for admins (`IW_VIEWER_SESSION_HOURS`, `IW_ADMIN_SESSION_MINUTES`).
- Brute-force limits: 5 wrong PINs per IP per 15 minutes, 50 per hour across all IPs, 1 second delay on each failure.
- Lost admin PIN: set `IW_VIEWER_PIN`, `IW_ADMIN_PIN` and `IW_RESET_PINS=1`, redeploy once, then set `IW_RESET_PINS=0` and clear the PINs again.

## Update paths

| Path | Who uses it | Auth |
| --- | --- | --- |
| **Pull sync** | The portal polls the data file every 5 minutes, validates it, keeps a history copy and swaps it in atomically. Source is either the GitHub data repo (`IW_GITHUB_REPO`) or any https URL (`IW_SOURCE_URL`, e.g. a view-only share link). | GitHub: fine-grained PAT, Contents read-only. URL: none or a secret link |
| **`POST /api/ingest`** | Manual pushes (`scripts/push-data.sh`), or the scheduled task if the hostname is ever allowlisted in the Claude sandbox egress policy. | `Authorization: Bearer <IW_INGEST_TOKEN>` |
| **`POST /api/sync`** | Force an immediate pull from the data source (`scripts/sync-now.sh`). | Same bearer token |

Both paths run the same validator (`app/validator.py`, a port of the original `validate.py`), refuse data older than what is live (unless `?force=1`), and keep the last 60 versions under `/data/history`.

**Open item (Oct 10, 2026):** the scheduled task still writes only to OneDrive. Its cloud sandbox can't reach `gronnevik.org` (egress allowlist) and the GitHub connector is read-only, so neither push route works from the task yet. See [docs/SCHEDULED-TASK.md](docs/SCHEDULED-TASK.md) for the options. Until one is chosen, update the portal with `scripts/push-data.sh` or by committing the file to the data repo yourself.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/login` | PIN sign-in (public) |
| GET | `/admin` | PIN management (admin) |
| POST | `/api/login`, `/api/logout` | Body `{"pin": "..."}` for login |
| POST | `/api/admin/pin` | `{"target": "viewer"|"admin", "new_pin", "current_admin_pin"}` (admin) |
| POST | `/api/admin/revoke` | Sign out all viewers (admin) |
| GET | `/` | Dashboard (viewer) |
| GET | `/industry-watch-data.js` | Live data in the `window.IW_DATA = {...};` format |
| GET | `/api/data` | Same data as JSON |
| GET | `/api/status` | Last update, item count, last sync result (viewer) |
| GET | `/healthz` | Container health check (public) |
| POST | `/api/ingest` | Body: data file (JS wrapper or bare JSON), max 2 MB |
| POST | `/api/sync` | Pull from GitHub now |

## Quick start

See **[docs/SETUP.md](docs/SETUP.md)** for the full Raspberry Pi, Portainer and Cloudflare walkthrough, and **[docs/SCHEDULED-TASK.md](docs/SCHEDULED-TASK.md)** for how the scheduled task writes data.

```bash
git clone https://github.com/glenngronnevik-oss/industry-watch-nordics.git
cd industry-watch-nordics
cp .env.example .env        # fill in the secrets and initial PINs
docker compose up -d --build
docker compose logs -f portal
```

## Development

```bash
pip install -r app/requirements.txt pytest anyio
python -m pytest -q tests
cd app && IW_DATA_DIR=/tmp/iw IW_INGEST_TOKEN=$(openssl rand -hex 32) IW_VIEWER_PIN=123456 IW_ADMIN_PIN=12345678 \
  IW_INSECURE_COOKIES=1 uvicorn main:app --reload --port 8080
```

## Security notes

- No inbound ports. Only `cloudflared` talks to the portal, over a private Docker network.
- Container runs as a non-root user with a read-only root filesystem, all capabilities dropped and `no-new-privileges`.
- Strict Content Security Policy (no inline scripts), `X-Frame-Options: DENY`, `noindex`.
- Ingest token compared in constant time; 10 failed attempts per IP per 10 minutes returns HTTP 429.
- Secrets live only in Portainer stack variables or `.env` (git-ignored). Never commit them.
- PIN-protected pages and data are sent with `Cache-Control: no-store` so Cloudflare and browsers don't cache them.
- A 6-digit PIN is a modest barrier on the open internet. Keep the Cloudflare rate-limiting rule from `docs/SETUP.md` step 3 in place, and consider a longer viewer PIN.
