#!/bin/zsh
set -euo pipefail

ROOT="/Users/rudolfkonfal/.openclaw/workspace/reporting-v2"
LOG_FILE="$ROOT/logs/morning-report.log"
cd "$ROOT"

mkdir -p "$ROOT/logs"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Load non-interactive shell env only. ~/.zshrc often assumes an interactive shell
# and has caused morning-report launchd runs to emit errors or abort setup.
for f in ~/.zshenv ~/.zprofile; do
  if [[ -f "$f" ]]; then
    source "$f" || echo "WARN: failed to source $f" | tee -a "$LOG_FILE"
  fi
done

ts() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "$*" | tee -a "$LOG_FILE"
}

REFRESH_STATUS=0
set +e
python3 - <<'PY' 2>&1 | tee -a "$LOG_FILE"
import os
import subprocess
import sys

root = os.environ.get('ROOT_OVERRIDE') or '/Users/rudolfkonfal/.openclaw/workspace/reporting-v2'
timeout = int(os.environ.get('MORNING_REPORT_REFRESH_TIMEOUT_SECONDS', '900'))
cmd = [sys.executable, 'scripts/refresh_data.py']

try:
    completed = subprocess.run(
        cmd,
        cwd=root,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        print(f'WARN: refresh_data.py exited with code {completed.returncode}; will try last successful generated report if valid', flush=True)
    raise SystemExit(completed.returncode)
except subprocess.TimeoutExpired:
    print(f'WARN: refresh_data.py timed out after {timeout} seconds; will try last successful generated report if valid', flush=True)
    raise SystemExit(124)
PY
REFRESH_STATUS=$?
set -e

if [[ "$REFRESH_STATUS" -eq 0 && "${AUTO_PUBLISH_PREVIEW:-0}" == "1" ]]; then
  python3 scripts/publish_preview.py 2>&1 | tee -a "$LOG_FILE"
fi

CHANNEL="${MORNING_REPORT_CHANNEL:-telegram}"
TARGETS_RAW="${MORNING_REPORT_TARGET:-}"
DETAIL_URL="${MORNING_REPORT_DETAIL_URL:-}"
MESSAGE_FILE="$ROOT/data/current/morning_report_previous_day_telegram.txt"
REPORT_JSON="$ROOT/data/current/morning_report_previous_day.json"

if [[ ! -f "$MESSAGE_FILE" ]]; then
  log "ERROR: Missing morning report message file: $MESSAGE_FILE"
  exit 1
fi

# Validate that the existing report is for yesterday before sending fallback data.
if ! python3 - <<'PY'
import json
from datetime import date, timedelta
from pathlib import Path
import sys

path = Path('/Users/rudolfkonfal/.openclaw/workspace/reporting-v2/data/current/morning_report_previous_day.json')
if not path.exists():
    print('ERROR: Missing morning report JSON file for validation', flush=True)
    raise SystemExit(1)

data = json.loads(path.read_text())
expected = (date.today() - timedelta(days=1)).isoformat()
actual = data.get('reportDate')
if actual != expected:
    print(f'ERROR: Existing morning report is stale, expected reportDate {expected}, got {actual}', flush=True)
    raise SystemExit(1)
print(f'Validated morning report fallback/source for reportDate {actual}', flush=True)
PY
then
  exit 1
fi

if [[ "$REFRESH_STATUS" -ne 0 ]]; then
  log "WARN: Using last successful generated morning report because refresh step failed (exit $REFRESH_STATUS)."
fi

MESSAGE_CONTENT="$(cat "$MESSAGE_FILE")"
if [[ -n "$DETAIL_URL" && "$MESSAGE_CONTENT" != *"$DETAIL_URL"* ]]; then
  MESSAGE_CONTENT="$MESSAGE_CONTENT

Detail: $DETAIL_URL"
fi

if [[ -z "$TARGETS_RAW" ]]; then
  log "ERROR: MORNING_REPORT_TARGET is empty"
  exit 1
fi

if [[ "$CHANNEL" != "telegram" ]]; then
  log "ERROR: Unsupported MORNING_REPORT_CHANNEL=$CHANNEL"
  exit 1
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  log "ERROR: TELEGRAM_BOT_TOKEN is not available"
  exit 1
fi

export TELEGRAM_BOT_TOKEN
export TARGETS_RAW
export MESSAGE_CONTENT
export MORNING_REPORT_DRY_RUN="${MORNING_REPORT_DRY_RUN:-0}"

python3 - <<'PY' 2>&1 | tee -a "$LOG_FILE"
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

def log(msg):
    print(msg)

token = os.environ['TELEGRAM_BOT_TOKEN']
message = os.environ['MESSAGE_CONTENT']
targets = [item.strip() for item in os.environ['TARGETS_RAW'].split(',') if item.strip()]
dry_run = os.environ.get('MORNING_REPORT_DRY_RUN') == '1'

for target in targets:
    if dry_run:
        log(f'[DRY RUN] Morning report would be delivered to {target}')
        continue
    payload = urllib.parse.urlencode({
        'chat_id': target,
        'text': message,
        'disable_web_page_preview': 'false',
    }).encode('utf-8')
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=payload,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    if not body.get('ok'):
        raise RuntimeError(f'Telegram send failed for {target}: {body}')
    message_id = body.get('result', {}).get('message_id')
    log(f'✅ Sent via Telegram. Message ID: {message_id}')
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log(f'[{stamp}] Morning report delivered to {target}')
PY
