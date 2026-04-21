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
- `requirements.txt`: root dependency file so Streamlit can deploy from repo root
- `frontend/`: the dashboard UI
- `backend/`: the API and sync logic used on the server-side deployment
- `scripts/`: helper scripts for API startup, sync loops, tmux sessions, and Cloudflare quick tunnels
- `services/`: `systemd` units for the always-on server setup

## Streamlit Cloud Setup

This repo is arranged so Streamlit Community Cloud can point at the repository root and use `streamlit_app.py` directly.

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
- Quick Tunnels are convenient for recovery and demos, but a named Cloudflare Tunnel is the better long-term production path.
