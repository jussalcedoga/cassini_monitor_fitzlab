# Cassini Monitor FitzLab

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://cassini-fitzlab.streamlit.app/)

A lightweight BlueFors monitoring stack for FitzLab:

- a remote FastAPI service that mirrors parquet readings into DuckDB
- a public API exposed through Cloudflare Tunnel
- a Streamlit dashboard for quick status checks from anywhere

## Live Links

- Streamlit dashboard: https://cassini-fitzlab.streamlit.app/
- Current public API base as of April 21, 2026: https://player-some-outside-bureau.trycloudflare.com
- Public health check: https://player-some-outside-bureau.trycloudflare.com/health

The Cloudflare Quick Tunnel URL changes whenever the API tunnel is restarted. Update your Streamlit secrets whenever that happens.

## What This Repo Includes

- `streamlit_app.py`: root entrypoint for Streamlit Community Cloud
- `requirements.txt`: root dependency file with a small, Streamlit-friendly pinned set
- `backend/`: the API and sync logic used on the server-side deployment
- `scripts/`: helper scripts for API startup, sync loops, tmux sessions, and Cloudflare quick tunnels
- `services/`: `systemd` units for the always-on server setup

## Streamlit Cloud Setup

This repo is arranged so Streamlit Community Cloud can point at the repository root and use `streamlit_app.py` directly. The dashboard is now self-contained at the repo root instead of importing a nested frontend module.

Use these app settings:

- Main file path: `streamlit_app.py`
- Python version: `3.12` if you want to match the server-side runtime closely

Use secrets like this:

```toml
api_base = "https://player-some-outside-bureau.trycloudflare.com"
api_key = "cassini"
dashboard_password = "cassini"
```

Generic version:

```toml
api_base = "https://YOUR_PUBLIC_API_HOSTNAME"
api_key = "cassini"
dashboard_password = "cassini"
```

A starter file is included at `.streamlit/secrets.toml.example`.

## Cloudflare API Setup

If you want to reproduce the same deployment pattern from raw BlueFors logs, this is the shortest path:

1. Put the BlueFors parquet export somewhere the host can read continuously.
   This deployment expects a warehouse-like tree rooted at `/jumbo/fitzlab/code/BlueFors Log DB/data/warehouse/readings`.
2. Configure the backend environment.
   Copy `backend/.env.example` to `backend/.env` and set at least:
   `WAREHOUSE_ROOT`, `API_KEY`, `API_HOST`, and `API_PORT`.
3. Create the backend environment and install the API dependencies.
   Use `python3 -m venv backend/.venv`, activate it, and install `backend/requirements.txt`.
4. Prime or update the DuckDB mirror from the BlueFors logs.
   Run `bash scripts/run_sync_once.sh` once for an initial sync, or enable the timer-backed service flow below.
5. Start the API locally on the host.
   Run `bash scripts/run_api.sh` and confirm `http://127.0.0.1:8001/health` is healthy if you are using the current default config.
6. Expose that local API through Cloudflare.
   For the quick-tunnel workflow in this repo, run `bash scripts/run_cloudflared_quick.sh`.
   Cloudflare will print a `https://...trycloudflare.com` URL that forwards to the local API port.
7. Put that Cloudflare URL into Streamlit secrets.
   Set `api_base` to the Cloudflare hostname, and keep `api_key` aligned with `backend/.env`.

For a more durable setup, install the provided `systemd` units:

```bash
bash scripts/install_systemd.sh
bash scripts/enable_services.sh
```

That gives you:

- `cassini-sync.timer` to keep ingesting fresh BlueFors files
- `cassini-api.service` to keep FastAPI serving locally
- `cloudflared-quick.service` as the quick-tunnel recovery path

If you prefer tmux instead of services for manual recovery, use:

```bash
RUN_SYNC_LOOP=1 bash scripts/start_tmux_stack.sh
```

The Streamlit app is also hardened against transient API hiccups: it caches the dashboard payload briefly and reuses the last successful snapshot during a failed refresh, so widget clicks do not immediately blank the page when the tunnel momentarily stutters.

## Remote Server Workflow

The backend lives on the remote server and reads from:

`/jumbo/fitzlab/code/BlueFors Log DB/data/warehouse/readings`

Useful entrypoints:

- `bash scripts/run_api.sh`
- `bash scripts/run_sync_once.sh`
- `bash scripts/run_sync_loop.sh`
- `bash scripts/run_cloudflared_quick.sh`
- `bash scripts/start_tmux_stack.sh`

If you want the sync loop inside tmux as well:

```bash
RUN_SYNC_LOOP=1 bash scripts/start_tmux_stack.sh
```

## Notes

- The sync layer now recovers automatically from stale lock files.
- The API launcher and Cloudflare quick tunnel both honor `backend/.env`, including `API_PORT`.
- The current live deployment is configured for `API_PORT=8001`.
- The API now exposes a `/dashboard` endpoint so the Streamlit app can fetch metrics and plots in one request when the backend has been restarted onto the updated code.
- Quick Tunnels are convenient for recovery and demos, but a named Cloudflare Tunnel is the better long-term production path.
