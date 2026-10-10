# Setup: Raspberry Pi, Portainer and Cloudflare Tunnel

Time needed: about 30 minutes. You need admin access to the `gronnevik.org` zone in Cloudflare, Portainer on the Pi, and this GitHub account.

## Push this code to your GitHub repo (one time)

From the folder where you unzipped `industry-watch-nordics.zip` (the `iwn` folder):

```bash
cd iwn
git init -b main
git add .
git commit -m "Initial import of Industry Watch portal"
git remote add origin https://github.com/glenngronnevik-oss/industry-watch-nordics.git
git pull --rebase origin main --allow-unrelated-histories   # merges the README GitHub created
git push -u origin main
```

Pushing `.github/workflows/` needs a token with the **workflow** scope (or use GitHub Desktop / `gh auth login`, which handle this). Check `git status` shows no `.env` file before you commit.

## 0. Prerequisites on the Pi

- Raspberry Pi 4 or 5 with a **64-bit** OS (Raspberry Pi OS Lite 64-bit or Ubuntu Server). Check with `uname -m`; it should print `aarch64`. 32-bit (`armv7l`) also works but builds are slower.
- Docker Engine with the compose plugin, and Portainer CE. If Docker is missing:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
less get-docker.sh            # read it before running
sudo sh get-docker.sh
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version
```

- Outbound internet from the Pi on TCP/UDP 7844 (Cloudflare Tunnel) and 443. No inbound ports or router port forwards are needed.

## 1. GitHub: two private repos and one read-only token

1. **Code repo:** `glenngronnevik-oss/industry-watch-nordics` (this repo).
2. **Data repo:** `glenngronnevik-oss/industry-watch-nordics-data`, holding a single file `industry-watch-data.js` that the scheduled task commits to.
3. Create a **fine-grained personal access token**: GitHub > Settings > Developer settings > Fine-grained tokens > Generate new token.
   - Name: `iw-pi-readonly`; expiration: 1 year (set a calendar reminder to rotate).
   - Repository access: *Only select repositories* > both repos above.
   - Permissions: **Contents: Read-only** (Metadata read-only is added automatically). Nothing else.
   - Copy the token once; you paste it into Portainer in step 4. Treat it like a password.

## 2. Generate the ingest token

On any machine:

```bash
openssl rand -hex 32
```

Keep the 64-character value for step 4 (`IW_INGEST_TOKEN`). It protects `POST /api/ingest` and `POST /api/sync`.

## 3. Cloudflare: create the tunnel and public hostname

1. Cloudflare dashboard > **Zero Trust** > **Networks** > **Tunnels** > **Create a tunnel**.
2. Connector type **Cloudflared**, name it `industry-watch-pi`, **Save tunnel**.
3. On the install screen choose **Docker**. The command shown ends in `--token eyJ...`. Copy only the long token after `--token`. That is `CLOUDFLARE_TUNNEL_TOKEN`. Do not run the command; the stack runs cloudflared for you.
4. **Next** > **Public hostnames** > **Add a public hostname**:
   - Subdomain: `industrywatchnordics`
   - Domain: `gronnevik.org`
   - Path: empty
   - Service type: `HTTP`
   - URL: `portal:8080` (the compose service name on the stack's private network)
5. Save. Cloudflare creates the proxied DNS record and the edge certificate automatically. HTTPS is terminated at Cloudflare; traffic from the edge to the Pi travels inside the encrypted tunnel.
6. Recommended edge settings for the zone (SSL/TLS section):
   - **Always Use HTTPS**: On
   - **Minimum TLS Version**: 1.2
   - **HSTS**: enable once you have confirmed the site works (max-age 6 months, include subdomains only if every subdomain is HTTPS).
7. **Recommended: rate-limit PIN guessing at the edge** (Security > WAF > Rate limiting rules > Create rule):
   - Expression: `(http.host eq "industrywatchnordics.gronnevik.org" and http.request.uri.path eq "/api/login")`
   - Characteristics: IP; 10 requests per 1 minute; action **Block** for 10 minutes.
   The free plan allows one rate-limiting rule, which is enough for this.
8. Optional hardening (Security > WAF > Custom rules): since the scheduled task uses GitHub sync, you can block `/api/ingest` and `/api/sync` from the internet and only call them from your LAN:
   - Expression: `(http.host eq "industrywatchnordics.gronnevik.org" and http.request.uri.path in {"/api/ingest" "/api/sync"})`
   - Action: **Block**

## 4. Portainer: deploy the stack from GitHub

1. Portainer > your environment > **Stacks** > **Add stack**.
2. Name: `industry-watch` (keep this name; the backup script expects the volume `industry-watch_iw-data`).
3. Build method: **Repository**.
   - Repository URL: `https://github.com/glenngronnevik-oss/industry-watch-nordics`
   - Repository reference: `refs/heads/main`
   - Compose path: `docker-compose.yml`
   - **Authentication**: on. Username `glenngronnevik-oss`, password = the fine-grained token from step 1.
   - Optional: **GitOps updates** on, polling every 5 minutes, so code changes you push to `main` redeploy automatically. Tick **Re-pull image and redeploy** only if you switch to the GHCR image (Option B below).
4. **Environment variables** (Advanced mode lets you paste them all at once):

```ini
IW_INGEST_TOKEN=<value from step 2>
IW_VIEWER_PIN=<initial viewer PIN, 6-12 digits>
IW_ADMIN_PIN=<initial admin PIN, 8-12 digits>
IW_RESET_PINS=0
IW_PUBLIC_HOST=industrywatchnordics.gronnevik.org
IW_GITHUB_REPO=glenngronnevik-oss/industry-watch-nordics-data
IW_GITHUB_BRANCH=main
IW_GITHUB_PATH=industry-watch-data.js
IW_GITHUB_TOKEN=<fine-grained token from step 1>
IW_SYNC_INTERVAL=300
# Optional instead of the GitHub settings: IW_SOURCE_URL=https://...
CLOUDFLARE_TUNNEL_TOKEN=<token from step 3>
```

5. **Deploy the stack**. The first deploy builds the image on the Pi (2 to 5 minutes on a Pi 4).
6. Check: **Containers** shows `iw-portal` as *healthy* and `iw-cloudflared` as *running*. In the cloudflared logs you should see `Registered tunnel connection` four times. The tunnel shows **Healthy** in the Cloudflare dashboard.

### Option B: use the prebuilt image from GitHub Container Registry

Every push to `main` runs `.github/workflows/image.yml`, which tests the code and publishes a multi-arch image (`linux/arm64`, `linux/amd64`) to `ghcr.io/glenngronnevik-oss/industry-watch-nordics:latest`.

1. Portainer > **Registries** > **Add registry** > Custom: URL `ghcr.io`, username `glenngronnevik-oss`, password = a classic token with only `read:packages` (fine-grained tokens do not cover GHCR yet).
2. In `docker-compose.yml`, comment out `build: .` and `image: industry-watch-nordics:local`, and uncomment the `ghcr.io` image line. Commit, then redeploy (or let GitOps pick it up).

### Command-line alternative (no Portainer)

```bash
git clone https://github.com/glenngronnevik-oss/industry-watch-nordics.git
cd industry-watch-nordics
cp .env.example .env && nano .env      # fill in the secrets
chmod 600 .env
docker compose up -d --build
docker compose ps
docker compose logs -f portal
```

Update later with `git pull && docker compose up -d --build`.

## 5. First sign-in and PIN housekeeping

1. Open https://industrywatchnordics.gronnevik.org. You land on the PIN screen.
2. Sign in with the admin PIN. The dashboard shows an **Admin** button next to **Sign out**; it opens `/admin`.
3. The PINs are now hashed in the data volume. In Portainer > Stacks > `industry-watch` > Editor, **clear the values of `IW_VIEWER_PIN` and `IW_ADMIN_PIN`** and click **Update the stack**. The portal keeps working with the stored hashes.
4. From now on, change PINs on `/admin`. Changing the viewer PIN signs out everyone who used the old one.
5. If the admin PIN is lost: set both PIN variables plus `IW_RESET_PINS=1`, update the stack, sign in, then set `IW_RESET_PINS=0` and clear the PINs again.

## 6. Seed and verify

The portal starts with an empty placeholder page. It fills in at the first GitHub sync (within 5 minutes) once the data repo has a file.

```bash
# Liveness (public)
curl -s https://industrywatchnordics.gronnevik.org/healthz
# Data and sync status: sign in and open /admin (Data status panel)

# Pull from GitHub immediately
IW_INGEST_TOKEN=... scripts/sync-now.sh

# Or push a local file directly
IW_INGEST_TOKEN=... scripts/push-data.sh ./industry-watch-data.js
```

The **Data status** panel on `/admin` (or `/api/status` when signed in) shows the last sync result. Typical values: `applied`, `unchanged`, `skipped (older than current data)`, `validation failed: ...`, `source returned HTTP 401` (token wrong or expired) or `HTTP 404` (repo, path or link wrong, or token lacks access).

## 7. Operations

| Task | How |
| --- | --- |
| View logs | Portainer > Containers > `iw-portal` > Logs |
| Back up data | `scripts/backup.sh` on the Pi (tarball of current data and 60 history versions) |
| Roll back | Copy a file from `/data/history` and push it with `FORCE=1 scripts/push-data.sh file.json` |
| Rotate ingest token | Change `IW_INGEST_TOKEN` in Portainer stack variables > **Update the stack** |
| Rotate GitHub token | New fine-grained token > update `IW_GITHUB_TOKEN` and the stack's repo credentials |
| Rotate tunnel token | Cloudflare tunnel > **Refresh token** > update `CLOUDFLARE_TUNNEL_TOKEN` |
| Update cloudflared | Bump the pinned tag in `docker-compose.yml` (see https://github.com/cloudflare/cloudflared/releases) |
| Test on the LAN | Uncomment the `ports:` block for `portal`, add `IW_INSECURE_COOKIES=1` (plain http only), redeploy, open `http://<pi-ip>:8080`. Remove both afterwards. |
| Change PINs | `/admin` while signed in with the admin PIN |

## Troubleshooting

- **502 / 1033 at Cloudflare:** tunnel is not connected or the service URL is wrong. Check cloudflared logs and that the public hostname points to `portal:8080` (not `localhost`).
- **Stack fails with "set IW_INGEST_TOKEN":** a required variable is missing in Portainer.
- **Portal exits with "First start: set IW_VIEWER_PIN and IW_ADMIN_PIN":** the data volume has no `auth.json` yet; add the initial PINs.
- **"cross-origin request refused" on sign-in:** `IW_PUBLIC_HOST` doesn't match the hostname in the browser.
- **Sign-in loops back to the PIN page on the LAN:** the cookie is `Secure`; use the HTTPS hostname or set `IW_INSECURE_COOKIES=1` for testing.
- **Ingest token rejected at startup:** it must be at least 32 characters.
- **Page says the data is several days old:** look at `/api/status`; if the data repo has new commits but `last_result` shows an error, fix that first. If the data repo has no new commits, check the scheduled task's last run.
