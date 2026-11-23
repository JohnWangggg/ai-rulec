# Changelog

## [0.16.1] - 2026-11-23

### Fixed

- `watch --health` 不再把 agent 健康扫描塞进轮询循环：一次扫描要 9–15 秒（7 个 agent × 多端点 × 重试），是 5 秒轮询间隔的两三倍，导致 `last_seen` 停滞、`status` 误报 `watch is stale`。现在扫描在后台线程跑，本轮先返回 `pending`，下一轮收结果；`_HEALTH_JOBS` 按 state 文件键控，同一时刻只有一个在飞。
- `waiting_for_config` 的日志去重标记带上 `waiting_cycles`，升级为「重启客户端」提示时仍会打印一次，之后不再每轮刷屏。

## [0.16.0] - 2026-11-23

`--latest` 现在指「客户端正在用的那份」，不再是「磁盘上最新落盘的那份」。

### Fixed

- `--latest`（`status` / `apply` / `rollback` / `doctor` / `watch` / `ui` / health 的代理推断）改为读取 FlClash 的 `currentProfileId`：新增 `profile.pick_profile()`，按「激活中 → 已纳管且带 AI 组 → 最新 mtime」三级回退。此前 FlClash 每次拉取订阅都会落盘新 profile，`--latest` 会静默指向一个未纳管的订阅（实测 `status --latest` 报 `ok False`、`groups 3`）。
- `watch --latest` 新增 `follow_active`：每轮重解析客户端当前 profile，用户在 FlClash 里换订阅后 watcher 跟着走，不再被启动时的路径钉死。
- `waiting_for_config` 不再无限等待：连续 `WAITING_LIMIT`(3) 轮看到「profile 已重放、config 仍无 AI provider」后升级为明确提示「重启客户端以加载 AI 规则」，并在 state 里记录 `waiting_cycles`；配置一旦被重载即清零。
- `status` 在 config 尚无 AI provider 条目时，不再误报 `provider cache incomplete: 0 missing, 0 mismatch`，改为 `runtime config has no AI providers; the client has not reloaded the profile yet`，并补上 `other` 计数。

### Changed

- `status` / `doctor` / `apply` / `rollback` / `watch` 统一走 `cli._latest_profile()`，没有可用 profile 时报 `<command>: no profiles discovered`，而不是抛 `IndexError`。
- `discover` 输出用 `[profile active managed]` 标注选中项，避免再靠 mtime 猜测。

## [0.15.0] - 2026-11-23

跨平台：同一份代码在 macOS、Linux、Windows 上走同一套路径与生命周期。

### Added

- 新增 `ai_rulec/platforms.py`：把 OS 差异收敛到一处 —— config/data/state/log 目录、代理客户端候选目录、sing-box/Hiddify 候选配置、`which_exe`（Windows 补 `.exe`）、`path_for_config`（配置内统一正斜杠）、`link_or_copy`（无符号链接权限时退回复制）。
- 原子写统一走 `platforms.atomic_replace()`：底层 `os.replace`，Windows 上客户端占用目标文件时按退避重试，耗尽后抛 `PermissionError` 并提示关闭占用文件的客户端，绝不写半成品。
- 新增 `service` 子命令（`install` / `status` / `describe` / `uninstall`，支持 `--meta`、`--interval`、`--agents-interval`、`--python`、`--dry-run`、`--json`）：macOS 注册 LaunchAgent，Linux 注册 systemd user unit，Windows 注册当前用户的计划任务；`describe` 只打印本平台会生成的定义，不落盘、不起进程。
- 新增 Linux 安装器 `scripts/install.sh` 与 Windows 安装器 `scripts/install.ps1`：与 `install.command` 同样的顺序（venv → mihomo → 来源 → 构建 → 应用 → 后台服务 → 控制台），服务注册一律交给 `ai-rulec service install`，脚本不直接调 `systemctl` / `schtasks`。两者都支持只打印不落盘的 `--dry-run` / `-DryRun`。
- CI 改为 `ubuntu-latest` / `windows-latest` / `macos-latest` 三平台矩阵（Python 3.11，`fail-fast: false`），并增加 `python -m ai_rulec --help` 与 `service describe --json` 冒烟；CI 不注册真实服务，也不连真实客户端。
- 新增文档 `docs/PLATFORMS.md`（支持矩阵、目录约定、服务承载方式、已知缺口）、`docs/LINUX.md`、`docs/WINDOWS.md`。
- 新增测试 `tests/test_platforms_core.py`、`tests/test_service.py`。

### Known limitations

- Linux 与 Windows 是**代码完成、未在真机验证**：目录解析、候选路径、原子写、服务定义都有单元测试，但没有真实 FlClash/Hiddify 客户端与真实流量的端到端结果。请先 `--dry-run` 再小步真实写入。
- 客户端配置目录是候选猜测，必须由 `discover` 在运行时验证；版本或打包方式不同就可能找不到。
- Surge 仍只在 macOS 有意义（依赖本机 `surge-cli`）；Hiddify 的真实写入仍未启用。

## 0.14.0 — 2026-11-22

日常使用不再需要记命令。

- `ai-rulec ui`：本地 Web 控制台，显示 AI 组出口、provider 缓存、代理端口、后台看护和 agent 连通性；按钮执行 `apply` / `doctor --fix` / `rollback` / `build`。仅绑 loopback，一次性 token，拒绝跨站请求。
- `install.command`：macOS 双击安装，串起 venv、mihomo、来源、构建、应用、LaunchAgent 和控制台。
- 路径解析不再依赖当前目录：`build` 记录最近输出，`apply` / `watch` / `merge` 省略 `--meta` 时自动找到它。
- mihomo 缺失不再静默跳过：`apply` / `merge` / `verify` 返回可操作的安装提示。
- `--version` 统一由 `ai_rulec.__version__` 驱动。

## 0.13.0 — 2026-11-22

首个公开 alpha。

- 服务目录：11 个 AI 服务（OpenAI/Anthropic/Gemini/Meta/Copilot/Cursor/Windsurf/xAI/Perplexity/Groq/OpenRouter），带角色、来源、置信度与质量 flags。
- 编译产物：mihomo YAML、sing-box rule-set v3、Surge `.list`；`build --formats all` 一次出齐，`verify` 实跑 `mihomo -t` / `sing-box check` / `surge-cli --check`。
- FlClash/mihomo：`apply` 幂等合并、`doctor` 修 hashed provider cache、`watch` 在订阅重写后自动重放、`rollback` 从备份恢复。
- sing-box/Hiddify：`singbox` 打补丁、`singbox-watch` 订阅重写后自动重打；Hiddify fork 字段只在临时校验副本里清洗。
- Surge：`surge` 定点 patch，校验失败不盲写；`.sgmodule` 不能加策略组，因此只做 profile patch。
- Agent 健康：`health` / `status --health` 检查 7 个 AI 工具的端点与 CLI；`watch --health` 按间隔汇总。
- 可靠性：probe 支持代理；缓存比较按 payload 忽略生成时间戳；watch 写 `last_seen`，`status` 报告 `stale`；health 默认重试 2 次。
- 许可证：代码 GPL-3.0-or-later；仓库不包含第三方规则数据或 `out*/` 生成物。

### 发布准备

- 新增 `docs/PROJECT.md`（名称、定位、范围、目标用户、差异点）、`SECURITY.md`、`PRIVACY.md`、`docs/TRADEMARKS.md`。
- 新增 `scripts/check_secrets.py`：扫描跟踪文件与 git 历史中的密钥、订阅 URI、绝对家目录、邮箱；已接入 CI。
- 扫描结果：0 命中；仓库与历史均无密钥、订阅 URI、真实 profile；测试夹具改用中性 profile id。
- 版本号统一到 `ai_rulec/__init__.py:__version__`，CLI `--version` 从包读取。
