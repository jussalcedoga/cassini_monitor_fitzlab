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
- keeping the backend alive with Task Scheduler
- deploying the Streamlit frontend on Streamlit Community Cloud

## Architecture

The deployment is intentionally simple:

1. BlueFors writes text logs into a Windows folder such as `C:\Users\Fitzlab\Bluefors logs\24-09-11\...`.
2. `scripts/run_sync_loop.sh` scans those day folders once per minute.
3. Changed day folders are re-parsed into the same `readings` schema the frontend already expects.
4. The backend writes a local DuckDB mirror at `backend/data/cassini.duckdb`.
5. FastAPI serves `/latest`, `/metrics`, `/history/{key}`, and `/dashboard`.
6. Cloudflare exposes that local API so Streamlit Cloud can reach it.

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
- `backend/app/`: FastAPI app, BlueFors parsers, DuckDB logic, and sync code
- `backend/data/`: local DuckDB mirror created on the backend host
- `scripts/`: bootstrap, sync, API, and tunnel runner scripts
- `services/`: optional Linux service files retained for manual use, but not required for the Windows deployment

## Streamlit Cloud Setup

Point Streamlit Community Cloud at the repository root:

- Main file path: `streamlit_app.py`

Use secrets like:

```toml
api_base = "https://YOUR_PUBLIC_API_HOSTNAME"
api_key = "cassini"
dashboard_password = "cassini"
```

For a quick tunnel, `api_base` will be the printed `https://...trycloudflare.com` URL.

For a named tunnel, `api_base` should be the stable hostname you configured in Cloudflare.

A starter file is included at `.streamlit/secrets.toml.example`.

## Windows Backend Quick Start

These steps are the recommended path on the machine that already has the BlueFors logs.

### 1. Install prerequisites

Install these on the Windows machine:

- Python 3.11 or newer
- Git for Windows
- Cloudflared

Git Bash is the easiest shell for the scripts in this repo. Cloudflared can be installed from Cloudflare and left on `PATH`, or you can place `cloudflared.exe` at the repo root.

### 2. Clone the repo on the Windows host

Example Git Bash flow:

```bash
cd /c/Users/Fitzlab
git clone https://github.com/jussalcedoga/cassini_monitor_fitzlab.git
cd cassini_monitor_fitzlab
```

### 3. Configure the backend

Copy the example environment file:

```bash
cp backend/.env.example backend/.env
```

Then edit `backend/.env` and set at least:

```dotenv
BLUEFORS_LOGS_ROOT="C:/Users/Fitzlab/Bluefors logs"
API_KEY=cassini
API_HOST=127.0.0.1
API_PORT=8001
READONLY_MAX_LAG_SECONDS=180
CLOUDFLARE_TUNNEL_TOKEN=
```

Use forward slashes in `BLUEFORS_LOGS_ROOT` even on Windows. That avoids escaping issues in `.env`.

### 4. Bootstrap the backend environment

From Git Bash:

```bash
bash scripts/windows_bootstrap.sh
```

That script creates `backend/.venv` and installs the backend dependencies.

### 5. Backfill the DuckDB mirror once

If you want to build the database once before starting the full stack:

```bash
bash scripts/run_sync_once.sh
```

On the first run, the sync scans every BlueFors day directory under `BLUEFORS_LOGS_ROOT` and builds the initial DuckDB mirror.

### 6. Start the live backend loop

For the simplest always-on workflow, open Git Bash and run:

```bash
bash scripts/run_windows_stack.sh
```

That one command starts:

- the 60-second sync loop
- the local FastAPI server on `127.0.0.1:8001`
- the Cloudflare tunnel runner

Keep that terminal open if you want the simplest visible deployment.

### 7. Verify locally

Once the stack is up:

- Sync log: `logs/windows-sync-loop.log`
- API log: `logs/windows-api.log`
- Tunnel log: `logs/windows-tunnel.log`

You can check local API health at:

- `http://127.0.0.1:8001/health`

## Production Recipe On Windows

If you want the simplest free setup, use this exact recipe:

1. Put the repo on the Windows machine that receives the BlueFors logs.
2. Set `BLUEFORS_LOGS_ROOT` in `backend/.env`.
3. Run `bash scripts/windows_bootstrap.sh`.
4. Run `bash scripts/run_sync_once.sh` once to backfill the entire database.
5. Leave `CLOUDFLARE_TUNNEL_TOKEN` blank so the stack uses a free quick tunnel.
6. Run `bash scripts/run_windows_stack.sh`.
7. Copy the printed `https://...trycloudflare.com` URL.
8. Put that URL into Streamlit secrets as `api_base`.
9. Register that same command in Windows Task Scheduler with an `At startup` trigger.

This gives you three layers of protection:

- the sync loop re-runs every minute
- the Windows stack supervisor restarts the sync loop, API, or tunnel if any of them exit
- Task Scheduler brings the whole stack back after a reboot

The tradeoff is that if the quick-tunnel URL changes after a reboot or restart, you will need to update `api_base` in Streamlit secrets.

## Build The Database From Scratch

This is the exact initial database build flow on the Windows host:

```bash
cd /c/Users/Fitzlab/cassini_monitor_fitzlab
cp backend/.env.example backend/.env
bash scripts/windows_bootstrap.sh
bash scripts/run_sync_once.sh
```

After the backfill finishes:

- the writable database will be at `backend/data/cassini.duckdb`
- the readonly snapshot will be at `backend/data/cassini_readonly.duckdb`

You can inspect the sync result in:

- `backend/logs/sync.log`
- `logs/sync-loop.log`

## Cloudflare Options

### Default: quick tunnel

If `CLOUDFLARE_TUNNEL_TOKEN` is empty, the runner uses a free quick tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8001
```

That prints a random public `https://...trycloudflare.com` URL in the terminal and in `logs/windows-tunnel.log`.

Use that URL as:

```toml
api_base = "https://your-random-subdomain.trycloudflare.com"
```

According to Cloudflare’s Quick Tunnel docs, this path is free, but it comes with important tradeoffs:

- the URL changes whenever the tunnel restarts
- Cloudflare does not promise uptime or SLA for quick tunnels
- quick tunnels have a 200 in-flight request limit
- quick tunnels do not support SSE

Official reference:

- Quick Tunnels: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/

### Optional: named tunnel

If you later want a stable public hostname, you can use a named Cloudflare Tunnel and place its token in `backend/.env`:

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=your_token_here
```

When `CLOUDFLARE_TUNNEL_TOKEN` is set, `scripts/run_cloudflare_tunnel.sh` automatically runs the named tunnel instead of a quick tunnel.

This is the best production path because:

- the hostname stays fixed across restarts
- Streamlit secrets do not need to change after reboots
- the deployment is much less brittle than quick tunnels

Step by step:

1. In the Cloudflare dashboard, go to Zero Trust.
2. Open Networks > Tunnels.
3. Create a new Cloudflare Tunnel for this backend.
4. Choose the Windows connector flow.
5. Add a public hostname that points to `http://127.0.0.1:8001`.
6. Copy the tunnel token.
7. Paste that token into `backend/.env` as `CLOUDFLARE_TUNNEL_TOKEN=...`.
8. Start the backend with `bash scripts/run_windows_stack.sh`.
9. Put the resulting fixed hostname into Streamlit secrets as `api_base`.

Official references:

- Tunnel tokens: https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/
- Useful tunnel commands: https://developers.cloudflare.com/tunnel/advanced/local-management/tunnel-useful-commands/

## Optional: Task Scheduler Instead Of A Visible Terminal

If you prefer not to keep a Git Bash window open, create a Windows Task Scheduler job that runs at startup.

Recommended settings:

- Name: `Cassini BlueFors Backend`
- Trigger: `At startup`
- General: `Run whether user is logged on or not`
- General: `Run with highest privileges`
- Conditions: uncheck `Start the task only if the computer is on AC power` if needed for your machine
- Settings: `If the task fails, restart every 1 minute`
- Settings: `Attempt to restart up to 3 times`
- Settings: `If the task is already running, then the following rule applies: Do not start a new instance`
- Program/script: `C:\Program Files\Git\bin\bash.exe`
- Start in: `C:\Users\Fitzlab\cassini_monitor_fitzlab`
- Add arguments:

```text
-lc "cd '/c/Users/Fitzlab/cassini_monitor_fitzlab' && bash scripts/run_windows_stack.sh"
```

Path note:

- `C:\Users\Fitzlab\cassini_monitor_fitzlab` is the normal Windows path and belongs in Task Scheduler's `Start in` field.
- `/c/Users/Fitzlab/cassini_monitor_fitzlab` is the Git Bash version of the same path and belongs inside the `bash -lc "cd ..."` command.
- You do not need `..` there. In Git Bash, the `C:` drive is mounted as `/c`.

If you use Task Scheduler with a quick tunnel, remember that the public URL may change after a reboot, which means `api_base` in Streamlit secrets may need to be updated again.

### If you want to run the tunnel separately

If you decide to manage the Cloudflare tunnel separately from the bash stack, you can disable the tunnel child process and only run sync + API:

```text
-lc "cd '/c/Users/Fitzlab/cassini_monitor_fitzlab' && RUN_TUNNEL=0 bash scripts/run_windows_stack.sh"
```

That is useful if you later move the tunnel onto a dedicated Cloudflare-managed Windows service.

## DuckDB Mirror Design

The backend keeps a local DuckDB mirror so the API stays fast and the dashboard can request time-series data without reparsing raw text files on every request.

The sync strategy is:

- treat each BlueFors day folder as one source unit
- compute a signature from file sizes and modification times
- only rebuild day folders whose contents changed
- replace rows for that day in the DuckDB database
- refresh a readonly snapshot after each successful sync pass

This keeps the minute-by-minute update loop lightweight while still allowing a full historical backfill from the original BlueFors logs.

## Useful Commands

Initial backfill:

```bash
bash scripts/run_sync_once.sh
```

Continuous live sync:

```bash
bash scripts/run_sync_loop.sh
```

Local API only:

```bash
bash scripts/run_api.sh
```

Tunnel only:

```bash
bash scripts/run_cloudflare_tunnel.sh
```

All-in-one Windows stack:

```bash
bash scripts/run_windows_stack.sh
```

## Troubleshooting

### The dashboard is stale

Check:

- `logs/windows-sync-loop.log`
- `http://127.0.0.1:8001/health`

If the local health endpoint is fresh but Streamlit is stale, the issue is usually the public tunnel URL or Streamlit secrets.

### The tunnel started but Streamlit still cannot connect

- If you are using a quick tunnel, make sure `api_base` matches the latest printed `trycloudflare.com` URL.
- If you are using a named tunnel, make sure the hostname points at the correct Cloudflare tunnel and the tunnel token is valid.

### The MXC temperature is missing

That usually means the BlueFors `CH6 T ...log` file is absent or the sensor is out of readable range. The sync preserves missing values as missing values rather than fabricating them.

### The first sync takes a while

That is normal. The first pass may backfill many daily folders. Later passes only rebuild folders that changed.

## Notes For Other Labs

This setup is meant to be reusable for other BlueFors systems with similar raw log directories. If your fridge writes the same family of daily text logs, you should be able to adapt this stack by:

- pointing `BLUEFORS_LOGS_ROOT` at your local BlueFors log root
- creating a Cloudflare tunnel for your local API
- setting the resulting `api_base` in Streamlit

The frontend is intentionally decoupled from the ingest path. As long as the API serves the same fields, the dashboard behavior stays the same.
