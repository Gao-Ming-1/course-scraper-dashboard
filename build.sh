#!/usr/bin/env bash
set -e

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo "==> Installing Playwright Chromium browser..."
playwright install chromium

echo "==> Installing Chromium OS-level dependencies..."
# playwright install-deps needs root; on Render it runs as root during build
playwright install-deps chromium || true

echo "==> Build complete."
