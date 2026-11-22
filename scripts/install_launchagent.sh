#!/usr/bin/env bash
# Install ai-rulec watch as a per-user LaunchAgent.
#
# Usage:
#   ./scripts/install_launchagent.sh install [--meta PATH] [--interval SECONDS]
#                                            [--agents-interval SECONDS]
#   ./scripts/install_launchagent.sh status
#   ./scripts/install_launchagent.sh uninstall
#
# The agent runs:
#   python3 -m ai_rulec watch --latest --meta <meta> --interval <seconds> \
#     --health --agents-interval <seconds> --json
# from the project directory, with logs under ~/Library/Logs.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.ai-rulec.watch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
OUT_LOG="$LOG_DIR/ai-rulec-watch.log"
ERR_LOG="$LOG_DIR/ai-rulec-watch.err.log"
META="$PROJECT_DIR/out/mihomo-meta.yaml"
INTERVAL="5"
AGENTS_INTERVAL="300"
PYTHON_BIN="${PYTHON:-$(command -v python3 || true)}"

usage() {
  sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    install|status|uninstall)
      ACTION="$1"; shift ;;
    --meta)
      META="$2"; shift 2 ;;
    --interval)
      INTERVAL="$2"; shift 2 ;;
    --agents-interval)
      AGENTS_INTERVAL="$2"; shift 2 ;;
    --python)
      PYTHON_BIN="$2"; shift 2 ;;
    --project)
      PROJECT_DIR="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
ACTION="${ACTION:-install}"

if [[ ! -f "$META" ]]; then
  echo "meta file not found: $META" >&2
  echo "run: python3 -m ai_rulec build --asn-mode drop" >&2
  exit 1
fi
META="$(cd "$(dirname "$META")" && pwd)/$(basename "$META")"

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "python3 not found; pass --python /absolute/path/to/python3" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import yaml' >/dev/null 2>&1; then
  echo "$PYTHON_BIN does not have PyYAML; pass --python with a working interpreter" >&2
  exit 1
fi

status() {
  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | sed -n '1,45p' || {
    echo "$LABEL is not loaded"
    return 1
  }
  echo "--- $ERR_LOG"
  tail -n 20 "$ERR_LOG" 2>/dev/null || true
  echo "--- $OUT_LOG"
  tail -n 10 "$OUT_LOG" 2>/dev/null || true
}

uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
}

install() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>-m</string>
    <string>ai_rulec</string>
    <string>watch</string>
    <string>--latest</string>
    <string>--meta</string>
    <string>$META</string>
    <string>--interval</string>
    <string>$INTERVAL</string>
    <string>--health</string>
    <string>--agents-interval</string>
    <string>$AGENTS_INTERVAL</string>
    <string>--json</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$OUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$ERR_LOG</string>
</dict>
</plist>
EOF

  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  if ! launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null; then
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      echo "$LABEL is already running"
    else
      echo "failed to start $LABEL" >&2
      exit 1
    fi
  fi
  echo "installed $LABEL"
  echo "  meta:     $META"
  echo "  interval: ${INTERVAL}s"
  echo "  plist:    $PLIST"
  echo "  logs:     $OUT_LOG"
  echo "            $ERR_LOG"
}

case "$ACTION" in
  install) install ;;
  status) status ;;
  uninstall) uninstall ;;
  *) usage >&2; exit 2 ;;
esac
