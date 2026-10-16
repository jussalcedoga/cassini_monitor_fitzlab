"""Watch the cloudflared service that exposes this API.

cloudflared runs as its own Windows service (CassiniTunnel) so that API
restarts never change the public quick-tunnel URL. This monitor:

- reads the current trycloudflare hostname from cloudflared's metrics server
  and records it in CURRENT_PUBLIC_URL.txt (plus a history log);
- probes the public URL and restarts cloudflared only when the tunnel is
  clearly dead (each restart issues a new URL, so it is deliberately
  conservative);
- starts the tunnel service again if it was left stopped.
"""
from __future__ import annotations

import http.client
import json
import logging
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from app.announce import UrlAnnouncer
from app.config import (
    PUBLIC_URL_FILE,
    REPO_ROOT,
    TUNNEL_METRICS_URL,
    TUNNEL_SERVICE_NAME,
    TUNNEL_WATCHDOG,
)

log = logging.getLogger("cassini.tunnel")

URL_HISTORY_FILE = REPO_ROOT / "logs" / "tunnel-url-history.log"
CREATE_NO_WINDOW = 0x08000000

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str, timeout: float, headers: Optional[dict] = None) -> tuple[Optional[int], bytes, Optional[BaseException]]:
    request = urllib.request.Request(url, headers={"User-Agent": "cassini-tunnel-monitor", **(headers or {})})
    try:
        with _opener.open(request, timeout=timeout) as response:
            return response.status, response.read(65536), None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(65536) if exc.fp else b"", None
    except (urllib.error.URLError, OSError) as exc:
        return None, b"", getattr(exc, "reason", exc)


def _doh_lookup(hostname: str) -> tuple[Optional[bool], Optional[str]]:
    """Resolve via Cloudflare DNS-over-HTTPS: (exists, ipv4).

    Windows and the LAN router cache "no such name" answers for up to 15
    minutes, so a hostname probed before its record existed can look dead
    locally while it works everywhere else. exists is None if DoH is unusable.
    """
    status, body, _ = _get(
        f"https://1.1.1.1/dns-query?name={hostname}&type=A",
        timeout=10,
        headers={"accept": "application/dns-json"},
    )
    if status != 200:
        return None, None
    try:
        data = json.loads(body)
    except ValueError:
        return None, None
    if data.get("Status") == 3:
        return False, None
    for answer in data.get("Answer") or []:
        if answer.get("type") == 1:
            return True, answer.get("data")
    return None, None


def _https_get(hostname: str, path: str, ip: Optional[str], timeout: float) -> tuple[Optional[int], bytes, Optional[BaseException]]:
    """GET https://hostname/path, connecting to ip when given (TLS/SNI still use hostname)."""
    conn = http.client.HTTPSConnection(hostname, 443, timeout=timeout, context=ssl.create_default_context())
    if ip:
        conn._create_connection = lambda _addr, t=None, src=None: socket.create_connection((ip, 443), t, src)
    try:
        conn.request("GET", path, headers={"User-Agent": "cassini-tunnel-monitor"})
        response = conn.getresponse()
        return response.status, response.read(65536), None
    except (OSError, http.client.HTTPException) as exc:
        return None, b"", exc
    finally:
        conn.close()


def _run(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)


class TunnelMonitor(threading.Thread):
    POLL_SECONDS = 30
    PROBE_SECONDS = 60
    PROBE_GRACE_SECONDS = 120          # let DNS for a brand-new hostname appear
    MISSING_RESTART_SECONDS = 600      # hostname no longer resolves (tunnel gone)
    UNREACHABLE_RESTART_SECONDS = 1800 # other public failures while internet works
    NOT_READY_RESTART_SECONDS = 1800   # cloudflared up but no edge connections
    METRICS_DOWN_RESTART_SECONDS = 600 # cloudflared hung (process alive, no metrics)
    MIN_RESTART_INTERVAL_SECONDS = 3600
    ENSURE_RUNNING_INTERVAL_SECONDS = 120

    def __init__(self):
        super().__init__(name="tunnel-monitor", daemon=True)
        self.stop_event = threading.Event()
        self.announcer = UrlAnnouncer()
        self.public_url: Optional[str] = self._saved_url()
        self._verified_url: Optional[str] = None
        self.hostname_seen_at = time.monotonic()
        self.metrics_ok = False
        self.ready_connections: Optional[int] = None
        self.last_probe: dict = {}
        self.restarts = 0
        self._bad_since: dict[str, float] = {}
        self._last_probe = 0.0
        self._last_restart: Optional[float] = None
        self._last_ensure = 0.0

    @staticmethod
    def _saved_url() -> Optional[str]:
        try:
            text = PUBLIC_URL_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text.splitlines()[0].strip() if text else None

    def run(self) -> None:
        self.stop_event.wait(10)
        while not self.stop_event.is_set():
            try:
                self.poll()
            except Exception:
                log.exception("tunnel monitor pass failed")
            self.stop_event.wait(self.POLL_SECONDS)

    def stop(self) -> None:
        self.stop_event.set()

    # --- state helpers -------------------------------------------------
    def _mark(self, kind: str, now: float) -> None:
        self._bad_since.setdefault(kind, now)

    def _clear(self, *kinds: str) -> None:
        for kind in kinds:
            self._bad_since.pop(kind, None)

    def _age(self, kind: str, now: float) -> float:
        since = self._bad_since.get(kind)
        return 0.0 if since is None else now - since

    # --- main pass -----------------------------------------------------
    def poll(self) -> None:
        now = time.monotonic()
        status, body, _ = _get(f"{TUNNEL_METRICS_URL}/quicktunnel", timeout=5)
        self.metrics_ok = status == 200
        if not self.metrics_ok:
            self.ready_connections = None
            self._mark("metrics_down", now)
            self._ensure_service_running(now)
        else:
            self._clear("metrics_down")
            try:
                hostname = (json.loads(body or b"{}").get("hostname") or "").strip()
            except ValueError:
                hostname = ""
            if hostname and f"https://{hostname}" != self.public_url:
                self._record_url(f"https://{hostname}")

            ready_status, ready_body, _ = _get(f"{TUNNEL_METRICS_URL}/ready", timeout=5)
            try:
                self.ready_connections = int(json.loads(ready_body or b"{}").get("readyConnections", 0))
            except (ValueError, TypeError):
                self.ready_connections = 0
            if ready_status == 200 and self.ready_connections > 0:
                self._clear("not_ready")
            elif self._internet_ok():
                self._mark("not_ready", now)

        if (
            TUNNEL_WATCHDOG
            and self.metrics_ok
            and self.public_url
            and now - self.hostname_seen_at >= self.PROBE_GRACE_SECONDS
            and now - self._last_probe >= self.PROBE_SECONDS
        ):
            self._last_probe = now
            self._probe_public(now)

        if TUNNEL_WATCHDOG:
            self._maybe_restart(now)

        # Announce a URL only once it has answered publicly, so the dashboard
        # never switches to a tunnel that is not reachable yet.
        if self.public_url and self.metrics_ok and (
            self._verified_url == self.public_url
            or (not TUNNEL_WATCHDOG and now - self.hostname_seen_at >= self.PROBE_GRACE_SECONDS)
        ):
            self.announcer.maybe_announce(self.public_url, now)

    def _record_url(self, url: str) -> None:
        previous = self.public_url
        self.public_url = url
        self._verified_url = None
        self.hostname_seen_at = time.monotonic()
        self._last_probe = 0.0
        self._bad_since.clear()
        try:
            PUBLIC_URL_FILE.write_text(url + "\n", encoding="utf-8")
            URL_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with URL_HISTORY_FILE.open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')} {url}\n")
        except OSError:
            log.exception("could not record public URL")
        log.warning("PUBLIC API URL is now %s (previously %s)", url, previous or "unknown")

    @staticmethod
    def _internet_ok() -> bool:
        status, _, _ = _get("https://www.cloudflare.com/cdn-cgi/trace", timeout=10)
        return status == 200

    def _probe_public(self, now: float) -> None:
        hostname = self.public_url.removeprefix("https://")
        started = time.monotonic()
        exists, ip = _doh_lookup(hostname)
        if exists is False:
            status, body, error = None, b"", "public DNS reports the hostname does not exist"
        else:
            status, body, error = _https_get(hostname, "/health", ip, timeout=20)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        self.last_probe = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "via": "edge-ip" if ip else "local-dns",
            "error": None if error is None else str(error),
        }

        if status == 200 and b'"status"' in body:
            self._clear("missing", "unreachable")
            self._verified_url = self.public_url
            return

        if not self._internet_ok():
            # Our own uplink is down; nothing a tunnel restart would fix.
            self._clear("missing", "unreachable")
            log.warning("public probe failed while internet is unreachable: %s", self.last_probe)
            return

        # Only authoritative public DNS may declare the tunnel gone; local
        # resolution failures can be stale negative-cache entries.
        if exists is False:
            self._mark("missing", now)
        else:
            self._mark("unreachable", now)
        log.warning("public probe failed: %s", self.last_probe)

    def _maybe_restart(self, now: float) -> None:
        reasons = []
        if self._age("missing", now) >= self.MISSING_RESTART_SECONDS:
            reasons.append("public hostname no longer resolves")
        if self._age("unreachable", now) >= self.UNREACHABLE_RESTART_SECONDS:
            reasons.append("public URL unreachable while internet works")
        if self._age("not_ready", now) >= self.NOT_READY_RESTART_SECONDS:
            reasons.append("cloudflared has no edge connections")
        if self._age("metrics_down", now) >= self.METRICS_DOWN_RESTART_SECONDS:
            reasons.append("cloudflared metrics endpoint unresponsive")
        if not reasons:
            return
        if self._last_restart is not None and now - self._last_restart < self.MIN_RESTART_INTERVAL_SECONDS:
            return
        self._restart_service("; ".join(reasons), now)

    def _restart_service(self, reason: str, now: float) -> None:
        self._last_restart = now
        self.restarts += 1
        self._bad_since.clear()
        log.error("restarting %s: %s", TUNNEL_SERVICE_NAME, reason)
        try:
            result = _run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Restart-Service -Name '{TUNNEL_SERVICE_NAME}' -Force",
                ],
                timeout=180,
            )
            if result.returncode != 0:
                log.error("restart of %s failed: %s", TUNNEL_SERVICE_NAME, (result.stderr or result.stdout).strip())
        except Exception:
            log.exception("restart of %s failed", TUNNEL_SERVICE_NAME)

    def _ensure_service_running(self, now: float) -> None:
        if now - self._last_ensure < self.ENSURE_RUNNING_INTERVAL_SECONDS:
            return
        self._last_ensure = now
        try:
            query = _run(["sc.exe", "query", TUNNEL_SERVICE_NAME], timeout=30)
            if query.returncode == 0 and "STOPPED" in query.stdout:
                log.warning("%s is stopped; starting it", TUNNEL_SERVICE_NAME)
                _run(["sc.exe", "start", TUNNEL_SERVICE_NAME], timeout=60)
        except Exception:
            log.exception("could not check %s", TUNNEL_SERVICE_NAME)

    def status(self) -> dict:
        return {
            "public_url": self.public_url,
            "cloudflared_metrics_ok": self.metrics_ok,
            "ready_connections": self.ready_connections,
            "last_public_probe": self.last_probe,
            "watchdog": TUNNEL_WATCHDOG,
            "watchdog_restarts": self.restarts,
            "problems": sorted(self._bad_since),
            "announce": self.announcer.status(),
        }
