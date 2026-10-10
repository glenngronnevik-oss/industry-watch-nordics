# How the scheduled task updates the portal

The Claude scheduled task **"Industry Watch dashboard refresh"** runs weekdays at 06:45 Oslo time in a cloud sandbox. Today it downloads the data and validator from OneDrive, researches news, validates, and uploads `Dashboards/Industry-Watch/industry-watch-data.js` back to OneDrive. That part is unchanged.

## Status (Oct 10, 2026): the last hop to the Pi is still open

Two constraints block a direct, unattended route from the task to the portal:

1. **Egress allowlist.** The sandbox only reaches hosts Tungsten has allowlisted. `industrywatchnordics.gronnevik.org` is not on it, so the task cannot call `POST /api/ingest`.
2. **Read-only GitHub connector.** The connector can read your repos but returns `403 Resource not accessible by integration` on every write, so the task cannot commit to the data repo.

The portal already supports all three routes below; pick one, and only configuration changes are needed.

| Option | What changes | Portal setting |
| --- | --- | --- |
| A. IT allowlists the hostname | Ask the Claude/AI platform owners at Tungsten to add `industrywatchnordics.gronnevik.org` to the sandbox egress allowlist. Then the task adds one step: `curl -X POST .../api/ingest -H "Authorization: Bearer <IW_INGEST_TOKEN>" --data-binary @industry-watch-data.js`. | `IW_INGEST_TOKEN` (already required) |
| B. Writable GitHub connector | If the GitHub connector gains Contents write access, the task commits the file to `industry-watch-nordics-data` and the portal polls it. | `IW_GITHUB_REPO`, `IW_GITHUB_TOKEN` |
| C. OneDrive view-only share link | Create an "anyone with the link, view" share for `industry-watch-data.js` (only if Tungsten policy allows anonymous links; check with Security first). Append `download=1` to the link. The task needs no change. | `IW_SOURCE_URL=<share link with download=1>` |

Note for option A: a scheduled task's prompt is stored in plain text, so the ingest token would sit there. Use a dedicated token and rotate it if the prompt is ever shared.

## Data contract

The file is `window.IW_DATA = {JSON};` (bare JSON is also accepted). Top-level keys: `updated` (ISO timestamp with Oslo offset), `updated_label`, `briefing`, `items`, `matrices`, `watchlist`, optional `window_days`, `recent_days`. Item rules are enforced by `app/validator.py`:

- Required: `id, date, category, vendor, title, summary, tungsten_product, signal, impact, tungsten_angle, source, url`; optional `added`, `date_note`.
- `category` in `PDF editors | Print management | Capture & workflow | Standards & format | Tungsten`.
- `signal` in `Threat | Opportunity | Parity | Watch`; `impact` in `High | Medium | Low`.
- `url` must be `https://`. No em dashes anywhere.
- Items older than `window_days` (default 240) are pruned, except category `Tungsten`; at most 60 items are kept.
- Data with an `updated` older than what is live is ignored (protects against stale re-runs).
