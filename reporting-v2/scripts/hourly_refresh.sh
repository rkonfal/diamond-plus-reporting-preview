#!/bin/zsh
set -euo pipefail

ROOT="/Users/rudolfkonfal/.openclaw/workspace/reporting-v2"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

python3 scripts/refresh_data.py
python3 scripts/build_order_bump_report.py

if [[ "${AUTO_PUBLISH_PREVIEW:-0}" == "1" ]]; then
  python3 scripts/publish_preview.py
fi
