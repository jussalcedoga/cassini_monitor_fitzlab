# Cassini Monitor FitzLab

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://cassini-fitzlab.streamlit.app/)

A Windows-first BlueFors monitoring stack for FitzLab:

- ingest the raw BlueFors text logs directly from the machine attached to the fridge
- mirror those logs into a local DuckDB database
- serve the data through a small FastAPI backend
- expose that backend through Cloudflare
- keep the Streamlit dashboard unchanged except for `api_base`

This repo now assumes the backend runs on the Windows machine that already has the live BlueFors logs. It does not require SSH access to a Linux host or a parquet export on Jumbo.

## Live App

- Streamlit dashboard: https://cassini-fitzlab.streamlit.app/
- GitHub repo: https://github.com/jussalcedoga/cassini_monitor_fitzlab

## Deployment Guides

- Windows BlueFors deployment checklist: [docs/BLUEFORS_WINDOWS_DEPLOYMENT_CHECKLIST.md](docs/BLUEFORS_WINDOWS_DEPLOYMENT_CHECKLIST.md)

If you want the shortest reusable guide for another BlueFors lab, start with that checklist. It covers:

- building the DuckDB mirror from raw BlueFors logs
- creating the Cloudflare tunnel on the Windows host
- keeping the backend alive as Windows services
- deploying the Streamlit frontend on Streamlit Community Cloud

## Architecture

```text
BlueFors logs ─► CassiniAPI service ──────────────────► CassiniTunnel service ─► Cloudflare ─► Streamlit Cloud
                 sync thread: polls every 10 s           cloudflared quick tunnel                 streamlit_app.py
                 DuckDB mirror + FastAPI on :8001        -> http://127.0.0.1:8001                 reads api_base
                 tunnel monitor + watchdog threads
```

1. BlueFors writes one line per minute into daily folders such as `C:\Users\Fitzlab\Bluefors logs\26-10-16\...`.
2. The **CassiniAPI** Windows service (one Python process) polls the newest day folders every 10 seconds and mirrors new rows into `backend/data/cassini.duckdb`. New readings reach the API within about 10 seconds of BlueFors writing them.
3. The same process serves `/health`, `/latest`, `/metrics`, `/history/{key}` and `/dashboard`. Payloads are computed once per data update and served from memory, gzip-compressed.
4. The **CassiniTunnel** Windows service runs `cloudflared` and publishes the API through a free Cloudflare quick tunnel. It is a separate service on purpose: restarting or updating the API never changes the public URL.
5. Streamlit Community Cloud runs `streamlit_app.py` and calls the API at `api_base`.

The frontend does not need to know whether the data came from raw BlueFors logs or from any older parquet-based pipeline. It only needs a working `api_base`.

## What The Backend Reads

The Windows ingest path is built around the standard BlueFors daily log files:

- `CH1 T ...log` -> `T_50K`
- `CH2 T ...log` -> `T_4K`
- `CH5 T ...log` -> `T_Still`
- `CH6 T ...log` -> `T_MXC`
- `Flowmeter ...log` -> `Flow`
- `maxigauge ...log` -> `P1` through `P6`
- `Channels ...log` -> `turbo_1`, `scroll_1`, `scroll_2`, `pulse_tube`

The runtime-hour cards shown in the dashboard are now integrated from the BlueFors state log instead of copied from a separate hardware counter. That keeps the API contract stable without depending on extra vendor-specific fields.

## Repo Layout

- `streamlit_app.py`: Streamlit Community Cloud entrypoint
- `backend/app/`: FastAPI app, BlueFors parsers, DuckDB store, sync worker, tunnel monitor
- `backend/data/`: local DuckDB mirror created on the backend host (not in git)
- `cassini.bat` + `deploy/cassini.ps1`: install and manage the Windows services
- `runtime/`: self-contained Python and the WinSW service wrapper (not in git; see the quick start)
- `scripts/`: one-off sync and legacy terminal runners
- `services/`: optional Linux service files retained for manual use, but not required for the Windows deployment

## Streamlit Cloud Setup

Point Streamlit Community Cloud at the repository root:

- Main file path: `streamlit_app.py`

Use secrets like:

```toml
api_base = "https://YOUR_PUBLIC_API_HOSTNAME"
api_key = "cassini"
dashboard_password = "cassini"

# Follow quick-tunnel URL changes automatically (values from backend/.env)
api_url_topic = "cassini-fitzlab-..."
api_url_key = "..."
```

For a quick tunnel, `api_base` will be the printed `https://...trycloudflare.com` URL.

For a named tunnel, `api_base` should be the stable hostname you configured in Cloudflare.

### Automatic URL follow (quick tunnels)

A quick-tunnel URL changes whenever cloudflared restarts, for example after a reboot. To keep the dashboard working without editing secrets each time:

1. The backend publishes its current public URL to a private [ntfy.sh](https://ntfy.sh) topic once the URL answers publicly, and again every 6 hours (ntfy keeps messages for 12 hours). Each message is `{"url", "ts", "sig"}`, where `sig` is HMAC-SHA256 of `url|ts` under a secret key.
2. The Streamlit app reads the newest message whose signature verifies and uses that URL. When a request fails, it re-checks within 30 seconds. Unsigned or forged messages are ignored, so knowing the topic is not enough to redirect the dashboard.
3. If the lookup fails or the secrets are absent, the app uses `api_base`, so it behaves exactly as before.

The topic and key are generated once and stored in `backend/.env` as `URL_ANNOUNCE_TOPIC` and `URL_ANNOUNCE_KEY`. Copy the same values into the Streamlit secrets as `api_url_topic` and `api_url_key`. `GET /health` shows the announcement status under `tunnel.announce`.

### Cooldown / warm-up markers

`GET /events` returns every cooldown and warm-up start in the full record. These are detected from the pulse tube switching on or off, ignoring blips shorter than 2 hours. The dashboard overlays them on every time-series plot, with a dashed line, a label, and light shading for the phase that follows. The fridge-state card also reports how long ago the current phase started. While the MXC thermometer is out of range (warm or precooling), the fridge state is derived from the 50 K / 4 K / Still stages and the pump states.

A starter file is included at `.streamlit/secrets.toml.example`.

## Windows Backend Quick Start

These steps set up the backend on the machine that already has the BlueFors logs. Everything runs as Windows services: nothing depends on an open terminal or a logged-in user, and it all comes back by itself after a reboot.

### 1. Get the repo and the runtime

```bash
cd /c/Users/Fitzlab/Desktop
git clone https://github.com/jussalcedoga/cassini_monitor_fitzlab.git
cd cassini_monitor_fitzlab
```

The services need three binaries that are not stored in git:

- `runtime/python/`: a self-contained CPython 3.12 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases), with the `install_only` Windows x64 build extracted here. Do not use the Microsoft Store Python, because it cannot run inside a Windows service.
- `runtime/bin/WinSW-x64.exe`: [WinSW v2.12.0](https://github.com/winsw/winsw/releases/tag/v2.12.0), the service wrapper.
- `cloudflared.exe` at the repo root: [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) for Windows x64.

Then install the backend dependencies into that Python:

```bash
runtime/python/python.exe -m pip install -r backend/requirements.txt
```

### 2. (Optional) Configure the backend

The defaults match the FitzLab machine. To change them, copy `backend/.env.example` to `backend/.env` and edit it:

```dotenv
BLUEFORS_LOGS_ROOT="C:/Users/Fitzlab/Bluefors logs"
API_KEY=cassini
```

Use forward slashes in `BLUEFORS_LOGS_ROOT` even on Windows. `API_KEY` must match `api_key` in the Streamlit secrets.

### 3. Install the services

Double-click `cassini.bat` and choose `install`, or from a terminal:

```bat
cassini.bat install
```

Approve the UAC prompt. The installer:

- registers **CassiniAPI** and **CassiniTunnel** as automatic-start services running as LocalSystem, with restart-on-failure (5 s, 15 s, then every 30 s)
- registers the **Cassini Service Guard** scheduled task, which runs at startup and every 5 minutes and starts either service if it was ever left stopped
- starts both services, waits for the API to answer and for the tunnel URL, and writes the URL to `CURRENT_PUBLIC_URL.txt`

On a fresh database the API serves the newest data within about 20 seconds, then backfills the full history newest-first. That takes about 5 minutes for two years of logs.

### 4. Point Streamlit at the tunnel

Copy the printed `https://...trycloudflare.com` URL (or run `cassini.bat url`) into the Streamlit secrets as `api_base`.

## Daily Operations

```bat
cassini.bat                  :: status: services, latest reading, public URL check
cassini.bat url              :: print the public URL and copy it to the clipboard
cassini.bat restart-api      :: restart the API only; the public URL stays the same
cassini.bat restart-tunnel   :: new cloudflared session; ISSUES A NEW URL
cassini.bat stop | start     :: stop or start both services (stop also pauses the guard task)
cassini.bat uninstall        :: remove the services and the guard task
```

Logs are in `logs/`:

- `CassiniAPI.err.log`: sync progress, errors, tunnel monitor events (Python logging goes to stderr)
- `CassiniAPI.out.log`: HTTP access log
- `CassiniTunnel.err.log`: cloudflared
- `tunnel-url-history.log`: every public URL the tunnel has had, with timestamps
- `deploy.log`: output of `cassini.bat` admin commands

WinSW rotates each log at 10 MB and keeps 5 files.

### Updating

- Frontend: push to `main`. Streamlit Community Cloud redeploys `streamlit_app.py` on its own.
- Backend: `git pull` on the backend host. The API watches `backend/app/*.py` and `backend/.env`; about 30-60 seconds after they change, it restarts itself to load the new code. The tunnel, and therefore the public URL, is not touched.

### When does the public URL change?

Quick-tunnel URLs are tied to the cloudflared process. The URL changes only when **CassiniTunnel** restarts: a reboot, `cassini.bat restart-tunnel`, or a rare cloudflared crash. API restarts, sync errors and network blips do not change it, because cloudflared reconnects to Cloudflare by itself with the same URL. With [automatic URL follow](#automatic-url-follow-quick-tunnels) configured, the dashboard switches to the new URL by itself within a few minutes. Without it, run `cassini.bat url` and paste the new URL into the Streamlit secrets as `api_base`.

## Reliability Design

| Failure | What happens |
| --- | --- |
| API process crashes | Service manager restarts it in 5 s; tunnel and URL unaffected |
| Sync loop hangs or dies | Watchdog thread exits the process after 15 min with no progress; the service restarts it |
| Service left stopped | The tunnel monitor (inside the API) restarts CassiniTunnel; the guard task restarts either service within 5 min |
| Network outage | cloudflared keeps retrying; same URL when the network returns |
| Quick tunnel dropped by Cloudflare | The hostname stops resolving; after 10 min (with internet otherwise working) the monitor restarts cloudflared and logs the new URL. It restarts at most once per hour |
| Reboot / power loss | Both services start at boot without anyone logging in; the dashboard picks up the new tunnel URL from the signed announcement |
| Idle sleep / hibernate | The API holds a Windows "system required" power request while it runs |
| A log line is half-written when read | The incomplete last line is skipped and picked up on the next pass |

Health at a glance: `http://127.0.0.1:8001/health` (or `<api_base>/health`) reports the latest reading time, `data_age_seconds`, a `stale` flag (no new data for 10 minutes), the last sync error, database size, and tunnel state.

## DuckDB Mirror Design

The backend keeps a local DuckDB mirror so the API stays fast and the dashboard can request time-series data without reparsing raw text files on every request.

- One process owns the database file. DuckDB allows a single writer process, so the sync worker and the API share one connection, with a cursor per thread. There is no cross-process locking and no snapshot copy.
- Each BlueFors day folder is one source unit. Its signature (total size + newest mtime) is read from the directory listing, so detecting changes across 700+ days takes about 0.15 s. Normal passes look only at new folders and the two newest days; a full rescan runs every 15 minutes.
- Past days are written once. The live day only replaces its last 5 minutes of rows on each update, so write churn is a handful of rows per minute.
- `readings` has no primary-key index. The original schema's index grew without bound under per-minute delete/insert churn (910k rows had bloated the old file to 9.3 GiB). The current file holds the full two-year history in 30-60 MB, because space freed by compaction is reused rather than growing the file.
- After a backfill, and daily after that, the table is rewritten in time order and checkpointed. This reclaims deleted rows and keeps time-window queries pruned to the relevant row groups.
- The parser parses timestamps in one vectorized pass (about 13x faster than per-row parsing) and produces identical rows.

## Rebuild The Database From Scratch

```bat
cassini.bat stop
move backend\data\cassini.duckdb backend\data\cassini.old.duckdb
cassini.bat start
```

The API rebuilds the mirror automatically, newest days first. Alternatively, with the services stopped, run `runtime\python\python.exe scripts\sync_once.py` to build it in the foreground.

## Cloudflare Options

### Default: quick tunnel

With no token configured, CassiniTunnel runs a free quick tunnel:

```bash
cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:20241 --url http://127.0.0.1:8001
```

Cloudflare's Quick Tunnel docs list important tradeoffs:

- the URL changes whenever the tunnel restarts
- Cloudflare does not promise uptime or SLA for quick tunnels
- quick tunnels have a 200 in-flight request limit
- quick tunnels do not support SSE

Official reference:

- Quick Tunnels: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/

### Optional: named tunnel

If you later want a hostname that never changes (this needs a domain on a Cloudflare account):

1. In the Cloudflare dashboard, go to Zero Trust > Networks > Tunnels and create a tunnel.
2. Add a public hostname that points to `http://127.0.0.1:8001`.
3. Copy the tunnel token.
4. Edit `runtime/services/CassiniTunnel.xml` and replace the `<arguments>` with `tunnel --no-autoupdate --metrics 127.0.0.1:20241 run --token YOUR_TOKEN`, then run `cassini.bat restart-tunnel`. Alternatively, run Cloudflare's own `cloudflared service install YOUR_TOKEN` and `cassini.bat` will leave it alone.
5. Put the fixed hostname into the Streamlit secrets as `api_base`.

Official references:

- Tunnel tokens: https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/
- Useful tunnel commands: https://developers.cloudflare.com/tunnel/advanced/local-management/tunnel-useful-commands/

## Legacy Terminal Runners

The Git Bash scripts in `scripts/` (`run_windows_stack.sh`, `run_api.sh`, `run_sync_loop.sh`, ...) still work for debugging, using `runtime/python` when present. `run_windows_stack.sh` refuses to start while the CassiniAPI service is running, because both would compete for port 8001 and the database file.

## Troubleshooting

### The dashboard says "Could not load data from the API"

1. Run `cassini.bat` (status). If the local API is healthy and the public `/health` check passes, the Streamlit `api_base` is out of date. Paste the URL from `cassini.bat url` into the secrets.
2. If the public check fails, look at `logs/CassiniTunnel.err.log` and `logs/CassiniAPI.err.log`.

### The dashboard is stale

Check `data_age_seconds` and `sync.last_error` in `http://127.0.0.1:8001/health`. If the sync has no errors but the data is old, BlueFors itself has stopped writing logs. Check the newest folder under `C:\Users\Fitzlab\Bluefors logs`.

### The MXC temperature is missing

That usually means the BlueFors `CH6 T ...log` file is absent or the sensor is out of readable range. The sync preserves missing values as missing values rather than fabricating them.

## Notes For Other Labs

This setup is meant to be reusable for other BlueFors systems with similar raw log directories. If your fridge writes the same family of daily text logs, you should be able to adapt this stack by:

- pointing `BLUEFORS_LOGS_ROOT` at your local BlueFors log root
- creating a Cloudflare tunnel for your local API
- setting the resulting `api_base` in Streamlit

The frontend is intentionally decoupled from the ingest path. As long as the API serves the same fields, the dashboard behavior stays the same.
