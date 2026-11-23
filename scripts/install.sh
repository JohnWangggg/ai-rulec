#!/bin/sh
# ai-rulec installer for Linux (Debian/Ubuntu, Fedora, Arch).
#
# Same order as install.command on macOS:
#   venv -> PyYAML -> mihomo check -> fetch sources -> build -> apply -> service -> web console
#
# The background watcher is registered by `ai-rulec service install`; this
# script never writes a unit file and never calls systemctl itself.
#
# Options:
#   --no-agent        skip the background service
#   --no-ui           do not open the local web console at the end
#   --skip-probe      skip endpoint probing during build
#   --no-mihomo       skip the mihomo verifier check
#   --python PATH     interpreter used to create the venv (default: python3)
#   --project DIR     project root (default: parent of this script)
#   --dry-run         print the commands that would run; change nothing
#   -h, --help        show this help
#
# Portable POSIX sh: no bash arrays, no `realpath -m`, no `pipefail`.

set -eu

say() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '   ! %s\n' "$*" >&2; }
die() { printf '\n错误: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Linux 安装器（Debian/Ubuntu、Fedora、Arch）

用法: scripts/install.sh [选项]

  --no-agent        不注册后台服务（ai-rulec service install）
  --no-ui           结束时不打开本地控制台
  --skip-probe      构建时跳过端点探测（更快）
  --no-mihomo       跳过 mihomo 校验器检查（仅跳过检查，不影响构建）
  --python PATH     用来创建 venv 的解释器（默认 python3）
  --project DIR     项目根目录（默认本脚本的上一级）
  --dry-run         只打印将要执行的命令，不写任何文件
  -h, --help        显示本帮助
EOF
}

WITH_AGENT=1
WITH_UI=1
WITH_PROBE=1
WITH_MIHOMO=1
DRY=0
PYTHON_BIN="${PYTHON:-}"
PROJECT_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --no-agent) WITH_AGENT=0 ;;
    --no-ui) WITH_UI=0 ;;
    --skip-probe) WITH_PROBE=0 ;;
    --no-mihomo) WITH_MIHOMO=0 ;;
    --dry-run) DRY=1 ;;
    --python)
      [ $# -ge 2 ] || die "--python 需要一个路径"
      PYTHON_BIN="$2"; shift ;;
    --python=*) PYTHON_BIN="${1#--python=}" ;;
    --project)
      [ $# -ge 2 ] || die "--project 需要一个目录"
      PROJECT_DIR="$2"; shift ;;
    --project=*) PROJECT_DIR="${1#--project=}" ;;
    -h|--help) usage; exit 0 ;;
    *) printf '未知参数: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

OS="$(uname -s 2>/dev/null || echo unknown)"
case "$OS" in
  Linux) ;;
  Darwin)
    die "this script is for Linux. On macOS use ./install.command instead." ;;
  *)
    die "this script is for Linux; detected $OS. Windows: scripts\\install.ps1" ;;
esac

if [ -n "$PROJECT_DIR" ]; then
  ROOT="$(CDPATH= cd -- "$PROJECT_DIR" && pwd)" || die "目录不存在: $PROJECT_DIR"
else
  ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)" || die "无法定位项目目录"
fi
[ -f "$ROOT/pyproject.toml" ] || die "$ROOT 里没有 pyproject.toml（用 --project 指定项目根目录）"

VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python3"
[ -n "${HOME:-}" ] || die "HOME 未设置；服务的日志与状态目录都基于它"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ai-rulec/log"
PROBE_ARGS=""

# build/apply 用相对路径（out/、config/ 覆盖），所以先固定工作目录
cd "$ROOT" || die "无法进入 $ROOT"

if [ "$WITH_PROBE" -eq 1 ]; then
  PROBE_ARGS="--probe --probe-proxy auto"
fi

run() {
  if [ "$DRY" -eq 1 ]; then
    printf '   [dry-run]'
    for _arg in "$@"; do printf ' %s' "$_arg"; done
    printf '\n'
    return 0
  fi
  "$@"
}

os_release_id() {
  [ -r /etc/os-release ] || return 0
  # shellcheck disable=SC1091
  . /etc/os-release
  printf '%s\n' "${ID:-} ${ID_LIKE:-}"
}

venv_hint() {
  case "$(os_release_id)" in
    *debian*|*ubuntu*) echo "sudo apt install python3-venv python3-pip" ;;
    *fedora*|*rhel*|*centos*) echo "sudo dnf install python3 python3-pip" ;;
    *arch*|*manjaro*) echo "sudo pacman -S python" ;;
    *) echo "安装发行版的 python3-venv / python3-pip 包" ;;
  esac
}

say "1/8 检查 Python"
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
fi
[ -n "$PYTHON_BIN" ] || die "未找到 python3（用 --python PATH 指定解释器）"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || [ -x "$PYTHON_BIN" ] \
  || die "解释器不可执行: $PYTHON_BIN"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v "$PYTHON_BIN")"

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  die "需要 Python >= 3.10，当前 $("$PYTHON_BIN" -V 2>&1)，用 --python PATH 指定"
fi
note "Python $("$PYTHON_BIN" -c 'import sys; print(sys.version.split()[0])')  ($PYTHON_BIN)"

if [ ! -x "$VENV_PY" ]; then
  if [ "$DRY" -eq 1 ]; then
    note "[dry-run] $PYTHON_BIN -m venv（跳过可写性探测）"
  elif _tmp="$(mktemp -d)" && "$PYTHON_BIN" -m venv "$_tmp/probe" >/dev/null 2>&1; then
    rm -rf "$_tmp"
    note "python3 -m venv 可用"
  else
    rm -rf "${_tmp:-}"
    die "python3 -m venv 不可用。请先安装：$(venv_hint)"
  fi
fi

say "2/8 准备虚拟环境"
if [ -x "$VENV_PY" ]; then
  note "已有 venv: $VENV"
else
  run "$PYTHON_BIN" -m venv "$VENV"
fi

pip_install() {
  if ! run "$VENV_PY" -m pip install -q "$@"; then
    warn "默认镜像源不可用，改用 pypi.org 重试…"
    run "$VENV_PY" -m pip install -q -i https://pypi.org/simple "$@"
  fi
}

pip_install --upgrade pip
pip_install --upgrade setuptools wheel
pip_install "PyYAML>=6.0"
pip_install --no-build-isolation -e "$ROOT"

if [ -x "$VENV_PY" ] && [ "$DRY" -eq 0 ]; then
  if ! "$VENV_PY" -c 'import yaml' >/dev/null 2>&1; then
    die "venv 里缺少 PyYAML。手动执行：$VENV_PY -m pip install PyYAML"
  fi
  note "PyYAML $("$VENV_PY" -c 'import yaml; print(yaml.__version__)')"
  "$VENV/bin/ai-rulec" --version
fi

say "3/8 检查校验器 mihomo"
if [ "$WITH_MIHOMO" -eq 0 ]; then
  note "已跳过（--no-mihomo）：build 照常进行，运行时不做 mihomo -t 校验"
elif command -v mihomo >/dev/null 2>&1; then
  note "mihomo $(mihomo -v 2>/dev/null | head -1)"
else
  warn "PATH 里没有 mihomo，构建时跳过配置校验。安装方式："
  note "发行版包（Arch: sudo pacman -S mihomo），或 GitHub release 的二进制/deb/rpm："
  note "https://github.com/MetaCubeX/mihomo/releases"
  note "装好后重新运行本脚本，或手动执行 ai-rulec verify"
fi

say "4/8 拉取规则源"
if [ -x "$ROOT/scripts/fetch_sources.sh" ]; then
  run "$ROOT/scripts/fetch_sources.sh" "$ROOT/.sources"
elif command -v bash >/dev/null 2>&1; then
  # fetch_sources.sh 需要 bash（它用了 pipefail），git 检出丢了执行位时走这里
  run bash "$ROOT/scripts/fetch_sources.sh" "$ROOT/.sources"
else
  die "找不到 $ROOT/scripts/fetch_sources.sh 或 bash"
fi

say "5/8 编译规则"
# shellcheck disable=SC2086  # PROBE_ARGS 需要按词拆成两个参数
run "$VENV/bin/ai-rulec" build --formats all --asn-mode drop $PROBE_ARGS

say "6/8 应用到当前代理客户端"
if ! run "$VENV/bin/ai-rulec" apply --latest; then
  die "apply 失败。先启动 FlClash/mihomo 再重试，或用 ai-rulec discover 确认客户端路径"
fi

if [ "$WITH_AGENT" -eq 1 ]; then
  say "7/8 注册后台服务（systemd user unit）"
  if ! run "$VENV/bin/ai-rulec" service install \
      --meta "$ROOT/out/mihomo-meta.yaml" --python "$VENV_PY" \
      --interval 5 --agents-interval 300; then
    die "service install 失败。查看：ai-rulec service describe"
  fi
else
  say "7/8 已跳过后台服务（--no-agent）"
fi

cat <<EOF

完成。常用命令：
  控制台:  $VENV/bin/ai-rulec ui
  状态:    $VENV/bin/ai-rulec service status
  日志目录: $LOG_DIR        （ai-rulec service status 会打印最近日志）
  卸载服务: $VENV/bin/ai-rulec service uninstall

EOF

if [ "$WITH_UI" -eq 1 ] && [ "$DRY" -eq 0 ]; then
  say "8/8 打开本地控制台"
  exec "$VENV/bin/ai-rulec" ui
fi
if [ "$WITH_UI" -eq 0 ]; then
  say "8/8 已跳过控制台（--no-ui）"
fi
exit 0
