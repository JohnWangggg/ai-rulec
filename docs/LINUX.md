# Linux 安装与运维

Linux 走和 macOS 相同的顺序：建 venv → 装依赖 → 检查 mihomo → 拉来源 → 编译 → 应用 → 注册后台服务 → 打开控制台。差别只在服务管理器（systemd user unit）和目录约定（XDG）。

> 当前状态：**已实现，未在真机验证**。先在副本上用 `--dry-run` 看一遍将执行的命令，再小步真实写入。逐项矩阵见 [PLATFORMS.md](PLATFORMS.md)。

## 支持范围

| 发行版 | 包管理器 | 需要额外装的包 |
|---|---|---|
| Debian 12+ / Ubuntu 22.04+ | `apt` | `python3`, `python3-venv`, `python3-pip`, `git`, `curl` |
| Fedora 38+ / RHEL 9+ | `dnf` | `python3`, `python3-pip`, `git`, `curl` |
| Arch / Manjaro | `pacman` | `python`, `git`, `curl` |

要求 Python **>= 3.10**。安装脚本会在建 venv 之前自检版本，并用临时目录试跑一次 `python3 -m venv`；失败时会按发行版打印对应的安装命令。

```sh
# Debian / Ubuntu
sudo apt install python3 python3-venv python3-pip git curl

# Fedora
sudo dnf install python3 python3-pip git curl

# Arch
sudo pacman -S python git curl
```

还需要 bash（`scripts/fetch_sources.sh` 用了 `pipefail`）。发行版默认都有。

## 安装

在仓库根目录执行：

```sh
./scripts/install.sh                 # 完整安装
./scripts/install.sh --no-agent      # 不注册后台服务
./scripts/install.sh --skip-probe    # 构建时跳过端点探测，更快
./scripts/install.sh --no-mihomo     # 跳过 mihomo 校验器检查
./scripts/install.sh --no-ui         # 结束时不打开本地控制台
./scripts/install.sh --python /usr/bin/python3.12
./scripts/install.sh --project /path/to/ai-rulec
./scripts/install.sh --dry-run      # 只打印将执行的命令
```

脚本本身是 POSIX sh（`/bin/sh` 即可），不依赖 bash 4 的语法，也不需要 `realpath`。

安装器只做前置检查和调用编排，**不自己写 systemd unit、不自己调 `systemctl`**。第 7 步是：

```sh
ai-rulec service install --meta out/mihomo-meta.yaml --interval 5 --agents-interval 300
```

服务定义、启停与日志重定向都由这个子命令负责；用 `ai-rulec service describe` 可以看到本机会写出什么。

`--no-mihomo` 只跳过「PATH 里有没有 mihomo」这项检查，构建流程不变；没有 mihomo 时 `build` 会跳过 `mihomo -t` 校验，产物仍会生成。事后想补校验：装上 mihomo 再跑 `ai-rulec verify`。

## mihomo 校验器

`apply` / `doctor` / `watch` 用 `mihomo -t` 验证配置能否加载。没有它时校验会被跳过，看起来像校验通过，所以安装器默认检查 PATH 里有没有 `mihomo`：

```sh
# Arch
sudo pacman -S mihomo

# 其他发行版：用 GitHub release 的二进制或 deb/rpm
# https://github.com/MetaCubeX/mihomo/releases
```

装好后确认：`mihomo -v`。

## 目录与日志

全部遵循 XDG（`XDG_CONFIG_HOME` / `XDG_DATA_HOME` / `XDG_STATE_HOME` 未设置时用默认值）：

| 用途 | 路径 |
|---|---|
| 可变状态（最近一次 build、watch 状态、事件） | `~/.local/state/ai-rulec/` |
| 日志 | `~/.local/state/ai-rulec/log/`（`platforms.log_dir()`） |
| 配置覆盖 | 工作目录下的 `config/services.yaml`、`config/agents.yaml`、`sources.yaml`（存在时优先于包内默认，不受 XDG 影响） |
| 构建产物 | 仓库内 `out/`、`.sources/`（不随仓库分发） |
| 客户端配置候选 | `~/.config/mihomo`、`~/.config/clash`、`~/.config/flclash`、`~/.config/clash-verge` 等，以 `ai-rulec discover` 的实际输出为准 |

看服务是否在跑、最近日志：

```sh
ai-rulec service status        # 是否安装/在跑 + 最近日志
ai-rulec service describe      # unit 文件路径、命令行、日志路径（不写任何文件）
journalctl --user -u ai-rulec-watch -n 50 --no-pager
```

unit 文件是 `~/.config/systemd/user/ai-rulec-watch.service`，日志是 `~/.local/state/ai-rulec/log/ai-rulec-watch.log` 与 `.err.log`。不要手写或手改 unit 文件：`ai-rulec service install` 重新渲染时会覆盖它。

## systemd 与 ssh 会话

后台服务是 **per-user** unit（`systemctl --user`）。这带来两个常见问题：

1. **没登录就停了。** 用户级 unit 默认随登录会话结束而停止。让它在注销/未登录时继续跑：

   ```sh
   sudo loginctl enable-linger "$USER"
   ```

   容器或最小化系统里可能没有 `logind`，此时 `enable-linger` 会失败，服务只在登录期间运行。`ai-rulec service install` 检测到没有 linger 时会在警告里提示这条命令。

2. **ssh 里没有 `DBUS_SESSION_BUS_ADDRESS` / `XDG_RUNTIME_DIR`。** 直接在 ssh 里跑 `systemctl --user ...` 可能报 `Failed to connect to bus`。先拿到用户总线环境：

   ```sh
   export XDG_RUNTIME_DIR="/run/user/$(id -u)"
   export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
   ```

   `ai-rulec service install` 只是调用 `systemctl --user`，**不会**替你补这两个变量；在 ssh 里安装前先设置好它们。

## 卸载

```sh
ai-rulec service uninstall        # 停止并移除后台服务，不动 profile 和产物
rm -rf .venv                      # 删掉本仓库的虚拟环境
rm -rf ~/.local/state/ai-rulec    # 构建记录、watch 状态、日志（可选）
rm -rf .sources out               # 拉下来的来源与产物（可选）
```

profile 侧的改动要自己回滚：`ai-rulec rollback --latest` 恢复 `apply` 之前的客户端配置备份；备份文件保留在客户端配置目录里，不会随卸载删除。

若之前 `enable-linger` 过，卸载后可以关掉：

```sh
sudo loginctl disable-linger "$USER"
```

`ai-rulec service uninstall` 只停用并删除 unit，不会动 linger 设置。

## 排错

**`python3 -m venv` 报 `ensurepip is not available`**
Debian/Ubuntu 把 venv 拆成了独立包：`sudo apt install python3-venv`。Fedora 上对应 `sudo dnf install python3`。

**venv 里缺 PyYAML**
安装器装完会显式 `import yaml` 验证。手动修：

```sh
.venv/bin/python3 -m pip install "PyYAML>=6.0"
```

**`mihomo: command not found`，构建时校验被跳过**
装上 mihomo（见上），或继续用 `--no-mihomo`，事后再 `ai-rulec verify`。

**`apply` 报找不到客户端 / `proxy port ... is not listening`**
先启动 FlClash 或 mihomo（服务不会替你启动代理），再用 `ai-rulec discover` 看候选路径，必要时用 `--root` 显式指定配置目录。

**改完 profile 不生效**
FlClash/mihomo 需要重启或重新加载 profile 后才会读到新配置；`watch` 只负责让规则和 provider cache 不漂移。

**`systemctl --user` 在 ssh 里不可用**
见上面的「systemd 与 ssh 会话」：设置 `XDG_RUNTIME_DIR` 与 `DBUS_SESSION_BUS_ADDRESS`，或直接跑 `ai-rulec service status` / `ai-rulec watch --once` 做排查。

**文件被占用**
Linux 上一般不会出现 Windows 那种强制文件锁；如果 `atomic_replace` 抛 `PermissionError`，检查目录属主与 SELinux/AppArmor 是否拦了写操作。
