#!/bin/bash
# Double-click installer for macOS. Terminal opens, everything else is automatic:
#   venv -> mihomo check -> fetch sources -> build -> apply to the running client -> background watcher -> web console
#
# Flags (for testing / advanced use):
#   --no-agent   skip the launchd watcher
#   --no-ui      do not open the web console at the end
#   --skip-probe skip endpoint probing during build
#   --no-mihomo  skip looking for / installing the mihomo validator
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
WITH_AGENT=1
WITH_UI=1
WITH_MIHOMO=1
PROBE_ARGS="--probe --probe-proxy auto"
for arg in "$@"; do
  case "$arg" in
    --no-agent) WITH_AGENT=0 ;;
    --no-ui) WITH_UI=0 ;;
    --skip-probe) PROBE_ARGS="" ;;
    --no-mihomo) WITH_MIHOMO=0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

pause_on_error() {
  local code=$?
  if [ "$code" -ne 0 ]; then
    echo
    echo "安装中断（退出码 ${code}）。把上面的报错发给我即可。"
    read -r -p "按回车关闭…" _
  fi
}
trap pause_on_error EXIT

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1/8 检查 Python"
PYTHON="${PYTHON:-python3}"
"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"需要 Python >= 3.10，当前 {sys.version.split()[0]}")
print("Python", sys.version.split()[0])
PY

pip_install() {
  if ! "$VENV/bin/python3" -m pip install -q "$@"; then
    echo "默认镜像源不可用，改用 pypi.org 重试…"
    "$VENV/bin/python3" -m pip install -q -i https://pypi.org/simple "$@"
  fi
}

say "2/8 准备虚拟环境"
if [ ! -x "$VENV/bin/python3" ]; then
  "$PYTHON" -m venv "$VENV"
fi
pip_install --upgrade pip
pip_install --upgrade setuptools wheel
pip_install --no-build-isolation -e "$ROOT"
"$VENV/bin/ai-rulec" --version

say "3/8 准备校验器 mihomo"
if [ "$WITH_MIHOMO" -eq 1 ]; then
  if command -v mihomo >/dev/null 2>&1; then
    mihomo -v | head -1
  elif command -v brew >/dev/null 2>&1; then
    brew install mihomo >/dev/null 2>&1 && mihomo -v | head -1 || echo "mihomo 安装失败，继续（跳过运行时校验）"
  else
    echo "未找到 brew，跳过 mihomo 安装（之后可 brew install mihomo）"
  fi
else
  echo "已跳过（--no-mihomo）"
fi

say "4/8 拉取规则源"
"$ROOT/scripts/fetch_sources.sh" "$ROOT/.sources"

say "5/8 编译规则"
"$VENV/bin/ai-rulec" build --formats all --asn-mode drop $PROBE_ARGS

say "6/8 应用到当前代理客户端"
"$VENV/bin/ai-rulec" apply --latest

if [ "$WITH_AGENT" -eq 1 ]; then
  say "7/8 安装后台看护"
  "$VENV/bin/ai-rulec" service install \
    --python "$VENV/bin/python3" \
    --meta "$ROOT/out/mihomo-meta.yaml" \
    --interval 5 \
    --agents-interval 300
else
  say "7/8 已跳过后台看护（--no-agent）"
fi

if [ "$WITH_UI" -eq 1 ]; then
  say "8/8 打开本地控制台"
  exec "$VENV/bin/ai-rulec" ui
fi

echo
echo "完成。随时运行：$VENV/bin/ai-rulec ui"
trap - EXIT
