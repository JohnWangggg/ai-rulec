#!/usr/bin/env bash
# Install ai-rulec singbox-watch as a per-user LaunchAgent (macOS).
#
# Usage:
#   ./scripts/install_singbox_agent.sh                # same as --print
#   ./scripts/install_singbox_agent.sh --print        # plist to stdout, no writes
#   ./scripts/install_singbox_agent.sh --install
#   ./scripts/install_singbox_agent.sh --uninstall
#
# Options:
#   --config PATH   sing-box/Hiddify profile to keep patched
#                   (default: ~/Library/Application Support/app.hiddify.com/data/current-config.json)
#   --interval N    poll interval in seconds (default: 5)
#   --python PATH   interpreter recorded in the plist (default: command -v python3)
#   --project DIR   project root, also WorkingDirectory (default: parent of this script)
#
# The agent runs, from the project root:
#   <python> -m ai_rulec singbox-watch <config> --interval <N> --json
#
# The CLI takes the config as a positional argument (it has no --config flag),
# so the path goes before the options. Logs live under ~/Library/Logs.
# Nothing is written or loaded unless --install (or --uninstall) is given.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="com.ai-rulec.singbox-watch"
HOME_DIR="${HOME:-}"
PLIST="$HOME_DIR/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME_DIR/Library/Logs"
OUT_LOG="$LOG_DIR/ai-rulec-singbox-watch.log"
ERR_LOG="$LOG_DIR/ai-rulec-singbox-watch.err.log"
DEFAULT_CONFIG="$HOME_DIR/Library/Application Support/app.hiddify.com/data/current-config.json"
CONFIG=""
INTERVAL="5"
PYTHON_BIN="${PYTHON:-$(command -v python3 || true)}"
ACTION="print"

usage() {
  awk 'NR > 1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print)
      ACTION="print"; shift ;;
    --install)
      ACTION="install"; shift ;;
    --uninstall)
      ACTION="uninstall"; shift ;;
    --config)
      [[ $# -ge 2 ]] || { echo "--config needs a path" >&2; exit 2; }
      CONFIG="$2"; shift 2 ;;
    --interval)
      [[ $# -ge 2 ]] || { echo "--interval needs a number" >&2; exit 2; }
      INTERVAL="$2"; shift 2 ;;
    --python)
      [[ $# -ge 2 ]] || { echo "--python needs a path" >&2; exit 2; }
      PYTHON_BIN="$2"; shift 2 ;;
    --project)
      [[ $# -ge 2 ]] || { echo "--project needs a path" >&2; exit 2; }
      PROJECT_DIR="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ -z "$HOME_DIR" ]]; then
  echo "HOME is not set" >&2
  exit 1
fi

if [[ ! "$INTERVAL" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "interval must be a number, got: $INTERVAL" >&2
  exit 2
fi

# Expand a leading ~ so the plist never carries a literal tilde.
if [[ -z "$CONFIG" ]]; then
  CONFIG="$DEFAULT_CONFIG"
else
  case "$CONFIG" in
    "~") CONFIG="$HOME_DIR" ;;
    "~/"*) CONFIG="$HOME_DIR/${CONFIG#\~/}" ;;
  esac
  [[ "$CONFIG" = /* ]] || CONFIG="$PWD/$CONFIG"
fi

if [[ -n "$PYTHON_BIN" && "$PYTHON_BIN" != /* ]]; then
  if [[ "$PYTHON_BIN" == */* ]]; then
    PYTHON_BIN="$PWD/$PYTHON_BIN"
  else
    PYTHON_BIN="$(command -v "$PYTHON_BIN" || true)"
  fi
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "python3 not found; pass --python /absolute/path/to/python3" >&2
  exit 1
fi
PYTHON_BIN="$(cd "$(dirname "$PYTHON_BIN")" && pwd)/$(basename "$PYTHON_BIN")"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

xml_escape() {
  local value="$1"
  # bash 5.2+ expands `&` in a replacement to the match; older bash does not.
  # Turning the feature off makes one spelling correct on every version.
  shopt -u patsub_replacement 2>/dev/null || true
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  printf '%s' "$value"
}

render_plist() {
  local program working config interval out_log err_log
  program="$(xml_escape "$PYTHON_BIN")"
  working="$(xml_escape "$PROJECT_DIR")"
  config="$(xml_escape "$CONFIG")"
  interval="$(xml_escape "$INTERVAL")"
  out_log="$(xml_escape "$OUT_LOG")"
  err_log="$(xml_escape "$ERR_LOG")"

  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$program</string>
    <string>-m</string>
    <string>ai_rulec</string>
    <string>singbox-watch</string>
    <string>$config</string>
    <string>--interval</string>
    <string>$interval</string>
    <string>--json</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$working</string>
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
  <string>$out_log</string>
  <key>StandardErrorPath</key>
  <string>$err_log</string>
</dict>
</plist>
EOF
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "launchctl is macOS only; --print still works elsewhere" >&2
    exit 1
  fi
}

install() {
  require_macos
  if ! "$PYTHON_BIN" -c 'import yaml' >/dev/null 2>&1; then
    echo "$PYTHON_BIN does not have PyYAML; pass --python with a working interpreter" >&2
    exit 1
  fi
  if [[ ! -f "$CONFIG" ]]; then
    echo "warning: config not found yet: $CONFIG" >&2
    echo "         the agent retries until Hiddify writes it" >&2
  fi
  if [[ ! -d "$PROJECT_DIR/out/singbox/AI-Rules" ]]; then
    echo "warning: $PROJECT_DIR/out/singbox/AI-Rules missing" >&2
    echo "         run: python3 -m ai_rulec build --formats sing-box" >&2
  fi

  mkdir -p "$HOME_DIR/Library/LaunchAgents" "$LOG_DIR"
  local tmp="$PLIST.tmp.$$"
  render_plist > "$tmp"
  plutil -lint "$tmp" >/dev/null
  mv "$tmp" "$PLIST"

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
  echo "  config:   $CONFIG"
  echo "  interval: ${INTERVAL}s"
  echo "  plist:    $PLIST"
  echo "  logs:     $OUT_LOG"
  echo "            $ERR_LOG"
}

uninstall() {
  require_macos
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  echo "logs left in place: $OUT_LOG"
}

case "$ACTION" in
  print) render_plist ;;
  install) install ;;
  uninstall) uninstall ;;
esac
