# Changelog

## [0.17.0] - 2026-11-23

按 `docs/AUDIT.md` 整改。三条 P0 都是真实的阻断问题，全部已修。

### Fixed

- **跨平台文件锁**：`singbox_watch.py` 顶层 `import fcntl` 在 Windows 上直接 `ModuleNotFoundError`，而 `unittest discover`（CI 的 windows-latest）会连带失败。新增 `platforms.FileLock`（Unix `fcntl.flock`、Windows `msvcrt.locking`，均非阻塞），`watch` 与 `singbox-watch` 共用；`watch` 原先在 Windows 上捕获 `ImportError` 后静默不加锁，现在两条路径都有互斥。
- **激活 profile 读取不再中断发现**：`_flclash_active_id()` 的 `plist.is_file()` 曾在 `try` 之外，沙盒或权限受限时 `PermissionError` 会打断整个 `discover_profiles()`。读取移入 `try`，异常一律降级为「未知激活项」。对应测试改为 mock `_active_hints()`，不再依赖宿主机真实 plist，并新增不可读 plist 的降级用例。
- **`apply` 不再把校验副本写进生产 profile**：`apply_profile()` 原先把注入 `geodata-mode` 的 validation 副本覆盖写回同一临时文件，再原子替换到目标 —— 用户 profile 会实际带上校验期的键。现在校验在独立 `TemporaryDirectory` 里完成，失败的 profile 一个字节都不动。
- **`apply` 的顺序**：拷贝规则与缓存修复原先都在 `mihomo -t` 校验之前，校验失败会留下已改动的 `AI-Rules`。现在顺序是「合并 → 校验 → 备份 → 拷规则 → 修缓存 → 原子替换」，任一环节失败即中止且不写 profile。
- **`provider cache repair` 失败会中止 apply**：运行中的 config 仍指向旧哈希路径，修不好就替换 profile 等于制造一个加载不出 provider 的配置。
- `python_executable()` 不再 `resolve()`：符号链接的 venv 会被解析成系统解释器，注册出去的服务跑在错误的环境里。
- 安装器补装 `setuptools`：Python 3.12+ 的 venv 不再预装，`pip install --no-build-isolation -e .` 会直接 `ModuleNotFoundError`。
- `service install` 在 Linux 安装器里显式传 `--python`，与 macOS/Windows 一致。
- `service._default_meta()` 改用 `paths.resolve_meta()`：原先指向 `site-packages/out/mihomo-meta.yaml`，wheel 安装后不存在；`WorkingDirectory` 一并改为 meta 所在目录（无仓库时回落到项目目录）。
- `service describe` 不再静默忽略 `--meta` / `--interval` / `--agents-interval` / `--python`，也不再为了打印而真的起子进程。
- **`probe` 的 DoH 走代理**：`--probe-proxy auto` 原先只代理目标连接，DoH 解析仍直连；在只能经代理出网的网络里，解析失败会被记成 `dns_fail`，再被默认的 `--probe-gate both` 整条剔除。现在 DoH 与目标连接同一出口。
- **`catalog` 死代码**：`hint_domain_mismatch` 因 `owner = hint_key or ...` 恒不触发；domain 级 `ip_asns` / `ip_cidrs` 从未写入，`broad_asn` 与置信度扣分永不生效。裸品牌别名（`meta`、`ai`）原先用 `endswith` 会误配到无关 TLD，改为锚定首段标签。
- **中文 Windows 的计划任务状态**：`schtasks /Query /FO LIST` 输出本地化（`状态: 正在运行`），英文匹配恒 False。改为优先读 `Get-ScheduledTask` 的状态枚举，文本匹配补中英文关键词。
- `health --attempts` 报的是实际上限而非实际次数，改为 `attempts`（实际）+ `attempts_limit`（上限）。
- `health --proxy auto` 在端口未监听时不再逐次烧超时，改为与 watcher 一致地切直连并提示。
- `ui` 的 AI 组过滤改用 `name == "AI" or name.startswith("AI-")`，不再把 `AIGC` / `AirPlay` 当 AI 组；页面补渲染 `warnings` / `errors`；子进程环境保留原 `PYTHONPATH`，未设 `SOURCE_ROOT` 时自动定位 `.sources`。
- `paths.path_for_config()` 真正展开 `~`（此前 docstring 与文档都声称展开）。
- `shutil.which` 全部收敛到 `platforms.which_exe`；`verify` 不再硬编码 `/Applications/Surge.app`，改用 `surge.find_surge_cli()`。
- `build` 未显式 `--out` 时固定写 `./out`（此前会跟随 `last-build.json` 写到旧目录），读取侧统一 `paths.resolve_out()`。

### Security

- `scripts/check_secrets.py --history` 原先用 `--format=` 清空提交头，只扫 diff 正文，漏掉 author/committer 邮箱。新增 `scan_identities()`，历史扫描会检查提交身份，并跳过 GitHub noreply 地址。
- 本仓库历史中的提交身份已重写为 noreply 地址（`git filter-branch`，标签同步重指）。

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
