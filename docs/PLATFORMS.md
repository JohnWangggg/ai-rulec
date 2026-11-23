# 系统支持

ai-rulec 的跨平台层集中在 `ai_rulec/platforms.py`：目录约定、客户端配置候选路径、原子替换（含 Windows 文件锁重试）、链接/复制、可执行文件查找、Python 解释器定位，全部由这一处决定，其余模块保持路径无关。脚本只负责检查前置条件并调用 `ai-rulec service`，不自己拼平台细节。

**结论先写：macOS 是参考实现，已实机验证；Linux 与 Windows 的代码路径和后台服务管理器已就位，但尚未在真机验证。** 下表中 macOS 列是可复现的事实，Linux / Windows 列标「未在真机验证」的地方表示代码存在、配置可生成，但没有在真实机器上跑过完整链路。

## 支持矩阵

| 能力 | macOS | Linux | Windows |
|---|---|---|---|
| 构建与产物（`build`，mihomo / sing-box / Surge） | ✓ 已实机验证 | 已实现，未在真机验证 | 已实现，未在真机验证 |
| 探测与健康检查（`probe`、`health`、`status --health`） | ✓ 已实机验证 | 已实现，未在真机验证 | 已实现，未在真机验证 |
| `apply` 到 FlClash / mihomo | ✓ 已实机验证（FlClash + 真实流量） | 未在真机验证 | 未在真机验证 |
| sing-box / Hiddify 补丁（`singbox`、`singbox-watch`） | 已支持；Hiddify 实机写入未启用（副本端到端已验证） | 未在真机验证 | 未在真机验证 |
| 后台服务（`service` 子命令） | ✓ launchd LaunchAgent | systemd user unit，未在真机验证 | Task Scheduler，未在真机验证 |
| 一键安装器 | ✓ `install.command`（双击）—— 它串起的各步骤均已在本机跑通，脚本本身未整段复跑 | `scripts/install.sh`，未在真机验证 | `scripts/install.ps1`，未在真机验证 |
| 本地 Web 控制台（`ui`） | ✓ 已实机验证 | 已实现，未在真机验证 | 已实现，未在真机验证 |
| Surge（`surge` 子命令） | 已支持，profile 副本验证通过 | 不适用（无 `surge-cli`） | 不适用（无 `surge-cli`） |

图例：✓ = 在本仓库的发布记录里有可复现的实机结果；「未在真机验证」= 代码路径已就绪、单元测试覆盖，但没有真实硬件/真实客户端的端到端证据；「不适用」= 目标程序在该系统上不存在。

单独成文的分平台说明见 [LINUX.md](LINUX.md) 与 [WINDOWS.md](WINDOWS.md)。

## 各层的实现位置

| 关注点 | 实现 | 说明 |
|---|---|---|
| 目录（config / data / state / log） | `ai_rulec/platforms.py` 的 `config_home`、`data_home`、`state_home`、`log_dir` | Unix 走 XDG 并以 `~/.config`、`~/.local/share`、`~/.local/state` 兜底；macOS 另加 `Library/Application Support` 与 `Library/Logs`；Windows 用 `APPDATA` / `LOCALAPPDATA` |
| 客户端配置候选目录 | `platforms.client_roots()` | 返回 `~` 相对字符串，按可能性排序；只是候选，必须由 `discover` 在运行时验证 |
| sing-box / Hiddify 候选配置 | `platforms.singbox_config_roots()` | 同上，含 Hiddify 的 `data/current-config.json` 与独立 sing-box 的 `config.json` |
| 原子写 | `platforms.atomic_replace()` | 底层 `os.replace`；Windows 被客户端占用时按 `retries`/`delay` 退避重试，耗尽后抛 `PermissionError` 并提示关闭占用文件的客户端 |
| 规则文件落地 | `platforms.link_or_copy()` | 优先符号链接，失败退回复制；Windows 无开发者模式时自动走复制（代理内核只读这些文件，语义等价） |
| 可执行文件查找 | `platforms.which_exe()` | `shutil.which` 的包装，Windows 上补试 `.exe` |
| 配置里的路径写法 | `platforms.path_for_config()` | 统一正斜杠、展开 `~`；mihomo / sing-box 在三平台都接受 `/` |
| 服务定义里的解释器 | `platforms.python_executable()` | 始终返回绝对路径，避免服务管理器拿到不同的 venv |

## 目录约定

| 函数 | macOS | Linux | Windows |
|---|---|---|---|
| `config_home()` | `$XDG_CONFIG_HOME` 或 `~/.config` | 同左 | `%APPDATA%`，缺省 `~/AppData/Roaming` |
| `data_home()` | `~/Library/Application Support` | `$XDG_DATA_HOME` 或 `~/.local/share` | `%LOCALAPPDATA%`，缺省 `~/AppData/Local` |
| `state_home()` | `$XDG_STATE_HOME` 或 `~/.local/state` | 同左 | `%LOCALAPPDATA%`，缺省 `~/AppData/Local` |
| `log_dir()` | `~/Library/Logs` | `state_home()/ai-rulec/log` | `%LOCALAPPDATA%\ai-rulec\logs` |

macOS 的 `config_home()` / `state_home()` 仍走 XDG 回退，而不是 `Library/Application Support` —— 这是 `platforms.py` 的现有行为，代理客户端的 Mac 专属目录由 `client_roots()` 单独给出（`~/Library/Application Support/com.follow.clash` 等）。

## 后台服务

三个平台共用同一个入口 `ai-rulec service`，命令语义一致：

```bash
ai-rulec service install     # 注册并启动后台 watch
ai-rulec service status      # 查看是否在跑、最近日志
ai-rulec service describe    # 打印本平台会生成的服务定义
ai-rulec service uninstall   # 停止并移除，不动 profile 和产物
```

每个动词都接受 `--meta PATH`、`--interval SEC`、`--agents-interval SEC`、`--python PATH`、`--dry-run`、`--json`。

三个平台的安装器都只调用这个子命令，自己从不写 unit、也不调 `launchctl` / `systemctl` / `schtasks`。macOS 早期那个单独写 plist 的 `scripts/install_launchagent.sh` 已删除，`ai-rulec service install` 是唯一入口。

| 平台 | 承载方式 | 日志 |
|---|---|---|
| macOS | per-user LaunchAgent（`launchd`，`~/Library/LaunchAgents/com.ai-rulec.watch.plist`） | `~/Library/Logs/ai-rulec-watch.log`、`.err.log` |
| Linux | systemd user unit | `$XDG_STATE_HOME/ai-rulec/log/`（默认 `~/.local/state/ai-rulec/log/`） |
| Windows | 当前用户的 Task Scheduler 任务（`ai-rulec-watch`，`/SC ONLOGON`） | 无持久日志文件（Task Scheduler 丢弃 stdout/stderr），用 `ai-rulec service status` 看状态 |

服务只负责规则 / cache 的生命周期与健康巡检，不会替你启动 FlClash、Hiddify 或 Surge；代理没启动时 `status` 会报 `proxy port ... is not listening`，这是预期行为。

两个平台特有的前提，`service install` 会警告但不会替你改系统：

- **Linux**：systemd user unit 在最后一个 session 结束时停止。要让它常驻，先执行一次 `sudo loginctl enable-linger $USER`；没开 linger 时 `install` / `status` 都会提示。
- **Windows**：计划任务用 `/SC ONLOGON`，即登录后启动；`/SC ONSTART`（开机即跑、早于登录）需要管理员 shell，安装器不会自动提权。

## 安装器

| 平台 | 入口 | 说明 |
|---|---|---|
| macOS | `./install.command`（Finder 双击） | 建 venv → 装 mihomo（有 brew 时）→ 拉来源 → 构建 → 应用 → `ai-rulec service install`（LaunchAgent）→ 开控制台 |
| Linux | `scripts/install.sh` | 同样的顺序；mihomo 只检查不安装（发行版包或 GitHub release 二进制），服务用 systemd user unit；支持 `--dry-run` |
| Windows | `scripts/install.ps1` | 同样的顺序；mihomo 只检查不安装（`mihomo.exe` 需自行放进 PATH），服务用 Task Scheduler；支持 `-DryRun` |

安装器失败时都会停在终端/窗口里显示具体报错，不静默跳过。三者的产物位置和命令行为一致，差异只在服务管理器与解释器路径。

## 已知缺口

- **Linux / Windows 未在真机验证**：配置生成、单元测试和纯 Python 逻辑有覆盖，但没有真实客户端（FlClash / Hiddify）与真实流量的端到端结果。请先 `--dry-run`，再小步真实写入。
- **客户端候选路径是猜测**：`client_roots()` 给的是常见安装位置，客户端的实际数据目录随版本和打包方式变化；以 `ai-rulec discover` 的输出为准，必要时用 `--root` 显式指定。
- **Surge 只在 macOS 有意义**：`surge` 子命令依赖本机 `surge-cli`，命令本身不检查 `os_family()`，只按 `surge.find_surge_cli()` 查找 —— Apple 的 App 内置路径仅在 macOS 上探测，其他平台只认 `PATH` 上的 `surge-cli`；找不到就报错退出，不会硬写。
- **Windows 文件占用**：FlClash 正在运行时可能持有 profile；`atomic_replace` 会重试，仍失败时会提示关闭客户端再重试，不会写半成品。
- **符号链接**：Windows 默认权限下无法创建符号链接，规则文件会退化为复制；两者的路由语义相同，但复制后源文件变化不会自动同步，需要重跑 `apply` / `watch`。
