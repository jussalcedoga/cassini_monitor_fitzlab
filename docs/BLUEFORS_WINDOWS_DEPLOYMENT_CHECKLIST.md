# BlueFors Windows Deployment Checklist

This checklist is for labs that already have a BlueFors dilution refrigerator writing its native daily text logs on a Windows machine and want:

- a local backend that mirrors those logs into DuckDB
- a public API exposed through Cloudflare
- a Streamlit dashboard that updates in real time from anywhere

This guide assumes the standard BlueFors-style folder structure:

```text
C:\Users\Fitzlab\Bluefors logs\24-09-11\...
```

The dashboard in this repo expects a backend API with the same schema used here. If your BlueFors installation writes the same family of daily log files, you can usually reuse this setup with only path and hostname changes.

## What You Need

- A Windows machine that has direct access to the live BlueFors log folder
- Administrator rights once, to install the Windows services
- Git for Windows
- A GitHub repository containing this app
- Nothing on Cloudflare's side for a quick tunnel (a Cloudflare account and domain only for a named tunnel)
- A Streamlit Community Cloud account

## 10-Minute Architecture

1. BlueFors writes raw text logs to the Windows machine.
2. The CassiniAPI service polls the newest day folders every 10 seconds.
3. New rows are parsed into a local DuckDB mirror.
4. FastAPI serves the latest values, histories, and metrics from DuckDB.
5. The CassiniTunnel service (cloudflared) exposes that local API to the internet.
6. Streamlit Cloud reads from the Cloudflare URL using `api_base`.

This keeps the frontend and backend separated cleanly:

- the Windows machine only needs to run the backend
- Streamlit Cloud only needs GitHub plus the API URL and API key

## Step 1. Clone The Repo On The BlueFors Windows Machine

Open Git Bash on the Windows machine and run:

```bash
cd /c/Users/Fitzlab
git clone https://github.com/jussalcedoga/cassini_monitor_fitzlab.git
cd cassini_monitor_fitzlab
```

## Step 2. Add The Runtime Binaries

The Windows services need three binaries that are not stored in git:

- `runtime/python/`: a self-contained CPython 3.12 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases), with the `install_only` Windows x64 build extracted here. Do not use the Microsoft Store Python, because it cannot run inside a Windows service.
- `runtime/bin/WinSW-x64.exe`: [WinSW v2.12.0](https://github.com/winsw/winsw/releases/tag/v2.12.0), the service wrapper.
- `cloudflared.exe` at the repo root: [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) for Windows x64.

Then install the backend dependencies into that Python:

```bash
runtime/python/python.exe -m pip install -r backend/requirements.txt
```

## Step 3. Configure The Backend

Copy `backend/.env.example` to `backend/.env` and set at least:

```dotenv
BLUEFORS_LOGS_ROOT="C:/Users/Fitzlab/Bluefors logs"
API_KEY=cassini
```

Notes:

- Use forward slashes in the Windows path inside `.env`.
- `API_KEY` must match `api_key` in the Streamlit secrets.
- For automatic URL follow, also set `URL_ANNOUNCE_TOPIC` (a random name such as `cassini-<lab>-<random>`) and `URL_ANNOUNCE_KEY` (64 random hex characters). See the README.

## Step 4. Install The Services

From a terminal in the repo (or by double-clicking `cassini.bat` and choosing `install`):

```bat
cassini.bat install
```

Approve the UAC prompt. This installs and starts:

- **CassiniAPI**: one Python process that mirrors the BlueFors logs into `backend/data/cassini.duckdb` every 10 seconds and serves the API on `127.0.0.1:8001`
- **CassiniTunnel**: `cloudflared`, which publishes the API through a free quick tunnel
- **Cassini Service Guard**: a scheduled task that starts either service if it is ever left stopped

Both services start at boot without anyone logging in, and they restart automatically if they crash.

On a fresh database the API serves the newest day within about 20 seconds, then backfills the full history newest-first. That takes about 5 minutes for two years of logs.

The installer prints the public `https://...trycloudflare.com` URL and writes it to `CURRENT_PUBLIC_URL.txt`.

Cloudflare's Quick Tunnel docs list these tradeoffs:

- the public URL changes whenever the tunnel restarts
- there is no SLA or uptime guarantee
- there is a 200 in-flight request limit
- SSE is not supported

Official reference:

- https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/

If you have a domain on Cloudflare and want a fixed hostname, see "Optional: named tunnel" in the README.

## Step 5. Verify The Backend Before Moving On

```bat
cassini.bat status
```

Healthy output shows both services `Running`, a latest reading less than a minute or two old, and `public /health OK`. The public check starts about 2 minutes after the tunnel starts.

You can also open `http://127.0.0.1:8001/health` on the Windows machine.

## Step 6. Deploy The Frontend On Streamlit Community Cloud

According to Streamlit’s Community Cloud deployment flow, you deploy by selecting the repository, branch, and entrypoint file, then optionally setting secrets and Python version in Advanced settings. Official docs:

- Deploy app: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- Secrets management: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- App settings: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings

Recommended steps:

1. Push your repo to GitHub.
2. Open Streamlit Community Cloud.
3. Click `Create app`.
4. Select:
   - Repository: your GitHub repo
   - Branch: `main`
   - Main file path: `streamlit_app.py`
5. Open `Advanced settings`.
6. Set Python version to `3.12` unless you have a reason to use another supported version.
7. Paste secrets like:

```toml
api_base = "https://your-public-api-hostname"
api_key = "cassini"
dashboard_password = "cassini"
# optional automatic URL follow (same values as backend/.env)
api_url_topic = "cassini-<lab>-<random>"
api_url_key = "<64 hex characters>"
```

8. Deploy.

After deployment:

- open the app
- confirm the latest timestamp is fresh
- if needed, open app `Settings` and update the `Secrets` tab later

## Step 7. Daily Operations Checklist

If the dashboard ever looks stale:

1. Run `cassini.bat status`. It shows the service states, the latest reading and its age, the last sync error, and the public URL check.
2. If the local API is healthy and the public check passes, but Streamlit is stale, compare `api_base` (or the URL-follow secrets) with `cassini.bat url`.
3. Check `logs/CassiniAPI.err.log` (sync and API) and `logs/CassiniTunnel.err.log` (cloudflared).
4. Verify the latest BlueFors day folder is still updating.

## What Makes This Robust

This design avoids the fragile parts of the older flow:

- no SSH, no remote parquet export, no separate Linux host: it ingests directly on the machine that receives the BlueFors logs
- one process owns a compact local DuckDB mirror; live updates touch only the last few minutes of rows
- Windows services with restart-on-failure, a guard task, a watchdog for a stalled sync, and a hold on idle sleep/hibernate
- the tunnel runs as its own service, so API restarts never change the public URL
- signed URL announcements let the dashboard follow quick-tunnel URL changes after reboots

## Good Fit And Limitations

Good fit:

- BlueFors systems writing the standard daily text logs
- labs that want a simple Windows-first deployment
- labs that want Streamlit Cloud for the frontend

Things to verify in a new lab:

- exact BlueFors file naming
- exact log root path
- whether all channels you care about are present
- firewall rules on the Windows machine if your environment is locked down

## Minimal Handoff Summary

If you hand only one block of instructions to another BlueFors lab, use this:

```bash
git clone https://github.com/jussalcedoga/cassini_monitor_fitzlab.git
cd cassini_monitor_fitzlab
# add runtime/python, runtime/bin/WinSW-x64.exe and cloudflared.exe (Step 2)
runtime/python/python.exe -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # set BLUEFORS_LOGS_ROOT
cassini.bat install                    # approve the UAC prompt
```

Then:

- deploy `streamlit_app.py` on Streamlit Community Cloud
- set `api_base`, `api_key`, `dashboard_password` (and optionally `api_url_topic` / `api_url_key`) in the Streamlit secrets
