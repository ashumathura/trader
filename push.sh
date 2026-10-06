#!/bin/bash
# Run once from inside this folder.
set -e
cd "$(dirname "$0")"
mkdir -p .github/workflows && cp workflow-update-data.yml .github/workflows/update-data.yml
git init -q -b main 2>/dev/null || true
git remote get-url origin >/dev/null 2>&1 || git remote add origin https://github.com/ashumathura/trader.git
git add -A
echo "Files about to be published:"; git diff --cached --name-only
git commit -q -m "Public market data pipeline" || true
git push -u origin main
