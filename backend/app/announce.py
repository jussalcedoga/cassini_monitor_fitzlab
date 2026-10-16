"""Announce the current public API URL so the dashboard can follow it.

Quick-tunnel URLs change whenever cloudflared restarts (e.g. after a reboot).
The URL is published to an ntfy topic as {"url", "ts", "sig"}, where sig is
HMAC-SHA256(key, "url|ts"). The Streamlit app reads the newest message whose
signature verifies, so nobody without the key can redirect the dashboard.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.request
from datetime import datetime
from typing import Optional

from app.config import URL_ANNOUNCE_KEY, URL_ANNOUNCE_SERVER, URL_ANNOUNCE_TOPIC

log = logging.getLogger("cassini.announce")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sign(url: str, ts: int, key: str) -> str:
    return hmac.new(key.encode(), f"{url}|{ts}".encode(), hashlib.sha256).hexdigest()


class UrlAnnouncer:
    RETRY_SECONDS = 120
    REFRESH_SECONDS = 6 * 3600  # ntfy.sh keeps messages for 12 hours

    def __init__(self, server: str = URL_ANNOUNCE_SERVER, topic: str = URL_ANNOUNCE_TOPIC, key: str = URL_ANNOUNCE_KEY):
        self.server = server
        self.topic = topic
        self.key = key
        self.announced_url: Optional[str] = None
        self.last_success: Optional[float] = None
        self.last_success_at: Optional[datetime] = None
        self.last_attempt: Optional[float] = None
        self.last_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.topic and self.key)

    def maybe_announce(self, url: str, now: float) -> None:
        if not self.enabled:
            return
        due = (
            url != self.announced_url
            or self.last_success is None
            or now - self.last_success >= self.REFRESH_SECONDS
        )
        if not due or (self.last_attempt is not None and now - self.last_attempt < self.RETRY_SECONDS):
            return
        self.last_attempt = now

        ts = int(time.time())
        body = json.dumps({"url": url, "ts": ts, "sig": sign(url, ts, self.key)}).encode()
        request = urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=body,
            method="POST",
            headers={"User-Agent": "cassini-monitor", "Title": "Cassini API URL"},
        )
        try:
            with _opener.open(request, timeout=15) as response:
                response.read()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("could not announce public URL: %s", self.last_error)
            return

        if url != self.announced_url:
            log.info("announced public URL %s", url)
        self.announced_url = url
        self.last_success = now
        self.last_success_at = datetime.now()
        self.last_error = None

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "announced_url": self.announced_url,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }
