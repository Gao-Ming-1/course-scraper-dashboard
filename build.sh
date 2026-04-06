#!/usr/bin/env bash
set -e

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo "==> Installing Playwright Chromium browser..."
playwright install chromium

echo "==> Installing Chromium system dependencies..."
playwright install-deps chromium

echo "==> Build complete."
