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
- Python 3.11+
- Git for Windows
- A GitHub repository containing this app
- A Cloudflare account
- A Streamlit Community Cloud account

## 10-Minute Architecture

1. BlueFors writes raw text logs to the Windows machine.
2. The sync loop scans those day folders every minute.
3. Changed day folders are parsed into DuckDB.
4. FastAPI serves the latest values, histories, and metrics from DuckDB.
5. Cloudflare exposes that local API to the internet.
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

## Step 2. Configure The Backend

Copy the example environment file:

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and set:

```dotenv
BLUEFORS_LOGS_ROOT="C:/Users/Fitzlab/Bluefors logs"
API_KEY=cassini
API_HOST=127.0.0.1
API_PORT=8001
READONLY_MAX_LAG_SECONDS=180
CLOUDFLARE_TUNNEL_TOKEN=
```

Notes:

- Use forward slashes in the Windows path inside `.env`.
- `READONLY_MAX_LAG_SECONDS` controls when API reads may prefer the main DuckDB file if the readonly snapshot lags.
- Leave `CLOUDFLARE_TUNNEL_TOKEN` blank until you create the tunnel.

## Step 3. Bootstrap Python

From Git Bash:

```bash
bash scripts/windows_bootstrap.sh
```

This creates `backend/.venv` and installs the backend dependencies.

## Step 4. Build The Database Once

Run the initial backfill:

```bash
bash scripts/run_sync_once.sh
```

Expected outputs:

- writable DB: `backend/data/cassini.duckdb`
- readonly DB: `backend/data/cassini_readonly.duckdb`
- sync log: `backend/logs/sync.log`

This first run can take time if you have a long BlueFors history.

## Step 5. Start With A Free Quick Tunnel

The default path in this repo is a free Cloudflare Quick Tunnel.

Leave this blank in `backend/.env`:

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=
```

When you run the backend stack, `cloudflared` will launch:

```bash
cloudflared tunnel --url http://127.0.0.1:8001
```

and print a random public `https://...trycloudflare.com` URL.

Use that URL as `api_base` in Streamlit secrets.

Cloudflare’s Quick Tunnel docs currently say this path is free, but it has tradeoffs:

- the public URL changes whenever the tunnel restarts
- there is no SLA or uptime guarantee
- there is a 200 in-flight request limit
- SSE is not supported

Official reference:

- https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/

## Step 6. Optional Stable Tunnel Later

If you later decide you want a fixed hostname, this repo also supports a named tunnel.

In that case:

1. Go to Cloudflare Zero Trust.
2. Open `Networks` > `Tunnels`.
3. Create a tunnel that points to `http://127.0.0.1:8001`.
4. Copy the token.
5. Paste it into `backend/.env`:

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=your_token_here
```

## Step 7. Start The Live Backend

Run:

```bash
bash scripts/run_windows_stack.sh
```

This launches:

- the 60-second sync loop
- the FastAPI server
- the Cloudflare tunnel runner

The Windows runner also supervises those processes and restarts them if one exits unexpectedly.

Useful logs:

- `logs/windows-sync-loop.log`
- `logs/windows-api.log`
- `logs/windows-tunnel.log`

## Step 8. Verify The Backend Before Moving On

Open these in a browser on the Windows machine:

- `http://127.0.0.1:8001/health`
- your Cloudflare hostname + `/health`

Healthy output should include:

- `"status": "ok"`
- a recent `latest_ts_eastern`
- a nonzero row count after the backfill

## Step 9. Make It Survive Reboots

Create a Windows Task Scheduler job.

Recommended settings:

- Name: `Cassini BlueFors Backend`
- Trigger: `At startup`
- General: `Run whether user is logged on or not`
- General: `Run with highest privileges`
- Settings: `If the task fails, restart every 1 minute`
- Settings: `Attempt to restart up to 3 times`
- Settings: `If the task is already running, do not start a new instance`

Action:

- Program/script:

```text
C:\Program Files\Git\bin\bash.exe
```

- Start in:

```text
C:\Users\Fitzlab\cassini_monitor_fitzlab
```

- Arguments:

```text
-lc "cd '/c/Users/Fitzlab/cassini_monitor_fitzlab' && bash scripts/run_windows_stack.sh"
```

This gives you three safety layers:

- the sync loop runs every minute
- the stack supervisor restarts child processes if they exit
- Task Scheduler restarts the full stack after a reboot

## Step 10. Deploy The Frontend On Streamlit Community Cloud

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
```

8. Deploy.

After deployment:

- open the app
- confirm the latest timestamp is fresh
- if needed, open app `Settings` and update the `Secrets` tab later

## Step 11. Daily Operations Checklist

If the dashboard ever looks stale:

1. Check local backend health at `http://127.0.0.1:8001/health`.
2. Check public backend health at your Cloudflare hostname + `/health`.
3. Check:
   - `logs/windows-sync-loop.log`
   - `logs/windows-api.log`
   - `logs/windows-tunnel.log`
4. Verify the latest BlueFors day folder is still updating.
5. If Cloudflare is healthy but Streamlit is stale, verify `api_base` in Streamlit secrets.

## What Makes This Robust

This design avoids the fragile parts of the older server-side flow:

- no SSH dependency
- no dependence on a remote parquet export
- no dependence on a separate Linux host
- direct ingest from the machine that actually receives the BlueFors logs
- local database mirror for fast reads
- a free quick tunnel by default, with named tunnel support kept optional
- scheduler plus supervision for restart behavior

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
cd /c/Users/Fitzlab
git clone https://github.com/jussalcedoga/cassini_monitor_fitzlab.git
cd cassini_monitor_fitzlab
cp backend/.env.example backend/.env
# edit backend/.env and set BLUEFORS_LOGS_ROOT
# leave CLOUDFLARE_TUNNEL_TOKEN blank for a free quick tunnel
bash scripts/windows_bootstrap.sh
bash scripts/run_sync_once.sh
bash scripts/run_windows_stack.sh
```

Then:

- add the same command to Windows Task Scheduler
- deploy `streamlit_app.py` on Streamlit Community Cloud
- set `api_base`, `api_key`, and `dashboard_password` in Streamlit secrets
- if the quick tunnel URL changes after a reboot, update `api_base` again
