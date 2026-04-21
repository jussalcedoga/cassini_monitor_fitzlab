#!/usr/bin/env bash
set -euo pipefail
systemctl enable --now cassini-api.service
systemctl enable --now cassini-sync.timer
systemctl status cassini-api.service --no-pager || true
systemctl status cassini-sync.timer --no-pager || true
