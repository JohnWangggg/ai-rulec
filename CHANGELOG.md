# Changelog

## [0.18.1] - 2026-11-23

把 Tower 产物发布成固定 HTTPS 地址，同时不越过数据许可证边界。

### Added

- **`build --redistributable-only`**：只使用 `sources.yaml` 里 `redistributable: true` 的来源；被排除的来源写进 `lock.json` 的 `excluded_sources`，每个来源的 `license` 与 `redistributable` 也进入 lock。
- **`publish-tower` 工作流**：每天 03:17 UTC 与手动触发，抓取来源、构建、`verify`，然后把 `ai-rules.yaml` 上传到固定 release 标签 `tower-latest`。Tower 里粘贴 `https://github.com/JohnWangggg/ai-rulec/releases/download/tower-latest/ai-rules.yaml` 即可。
- 公开产物当前只含 **MIT 的 v2fly**。GPL-2.0-only（bm7）、无许可证（G4free）、许可证未确认（Sukka / jinx / ma-wenqian）与 GPL-3.0 聚合源（Loyalsoldier）都不进入公开文件；本地完整构建不受影响。

### Docs

- README 的 Tower 段补「链接导入」与固定地址；`docs/LICENSE-ASSESSMENT.md` 增加公开产物开关的约束。

## [0.18.0] - 2026-11-23

新增 `tower` 格式：一份自包含的 Clash 规则方案，直接导入 Tower。

### Added

- **`build --formats tower`**：生成 `out/tower/ai-rules.yaml`。每个服务一个 `AI-<service>` select 组（`include-all-proxies: true`），父组 `AI` 汇总，规则为 `RULE-SET,<service>,AI-<service>` + `MATCH,AI`。`--map <service>=<policy>` 可把初始成员换成已有策略；`--asn-mode drop` 生效。
- **Tower 产物是单文件**：Tower 的 Clash 解析器只接受 `type: inline` 的 `rule-providers`，`type: file` + `path` 会抛 `unsupportedSyntax`；因此规则内联进 `payload`，不产出散装 provider 文件，也不依赖任何本地路径。
- **`verify` 校验 Tower 产物**：每个 provider 必须是 `inline`、不得含 `url`/`path`/`header`/`headers`/`exclude-filter`、payload 行前缀合法、`RULE-SET` 的 provider 与目标组都存在、裸规则只允许 `MATCH`/`FINAL`；有 mihomo 时再跑一遍 `mihomo -t`（同一份文件 mihomo 也认）。
- 用 Tower 自己的 `SchemeYAMLReader`（`parser.swift` 内实现）实测读取生成物：12 组 / 11 provider / 12 条规则全部解析成功。

### Docs

- README 增「Tower」客户端行与 `### Tower` 段：导入步骤（规则 → `+` → 文件，或 HTTPS 链接）、`include-all-proxies` 行为、`--map` 用法、沙盒限制。

## [0.17.2] - 2026-11-23

跨平台 CI 从「三平台全红」修到「三平台全绿」。功能不变，修的是可移植性与测试隔离。

### Fixed

- **`xml_escape` 在 bash 5.2+ 丢掉转义**：`${value//&/\&amp;}` 在 `patsub_replacement` 开启时把 `\&` 当字面量，产出 `a\&amp;b`，`install_singbox_agent.sh --print` 生成的 plist 会带反斜杠。现在先 `shopt -u patsub_replacement` 再替换，bash 3.2 / 5.3 输出一致。
- **`find_surge_cli` 在 Windows 上漏掉 app bundle**：原用 `os.path.sep` 判断「绝对路径」，Windows 的 `\` 让 `/Applications/Surge.app/...` 被当成裸命令名过滤。改为判断 `"/"`。
- **Windows 检出可能带 CRLF，`bash -n` 直接失败**：新增 `.gitattributes`，`*.sh` / `*.command` 固定 LF，`*.ps1` 固定 CRLF，图片标 binary。
- **`LinuxInstallTests` 读宿主 linger 状态**：`/var/lib/systemd/linger/$USER` 在 CI runner 上存在、在笔记本上通常不存在，导致 `test_linger_warning_when_disabled` 时红时绿。`PlatformHarness` 现在默认 patch `_linger_enabled=False`，警告路径确定；systemd 单测改为断言 `_systemd_arg()` 渲染后的词，Windows 路径不再因 `\` 双写而失败。
- **脚本测试在 Windows 上假绿**：`install.command`（macOS）、`scripts/install.sh`（Linux）、`scripts/install_singbox_agent.sh`（launchd）在三平台 runner 上都会因 git-bash 的 `bash -n` 行为不同而失败。按 `os.name` / `sys.platform` 跳过，不再依赖 PATH 上碰巧存在的 bash。

### Docs

- `PROJECT.md` / `RELEASE.md` 同步 0.17.2、最新 tag `v0.17.1`、170 个用例与 wheel 名。

## [0.17.1] - 2026-11-23

客户端关闭时不再用假失败换取假信号。

### Fixed

- **`status --health` / `health` 不再静默改走直连**：`--proxy auto` 解析到代理端口后，若端口未监听，原先会回落直连逐个 agent 探测。7 个 agent × 多端点 × 重试耗时 72 秒，最终报 5 个 `down` —— 这些失败只说明客户端没运行。现在直接跳过本轮并说明原因（`agents: skipped (<url> is not listening)`），实测 0.5 秒返回；`health` 的 `--json` 输出 `{"ok": null, "skipped": ...}`，退出码 2。
- 显式 `--proxy direct` 仍然照常检查：直连是用户指定的路由，不是回退。
- `watch` 的 agent 扫描同样跳过，结果行显示 `agents=skipped`，不再把直连超时写成 `down=`。
- 网页控制台在跳过时显示「客户端未运行」而不是「0 ok / 0 降级 / 5 断开」。
- **`service describe` 在首次构建前不再失败**：`_plan()` 增加 `require_meta`，`describe()` 允许 meta 尚不存在（报 `meta_exists: false`）；三平台 CI 空目录下退出码为 0。测试改用 `_default_meta` 隔离，不再依赖仓库里碰巧存在的 `out/`。
- **后台服务工作目录回到仓库根**：`_work_dir()` 在 `meta` 位于 `<repo>/out/` 且上级含 `pyproject.toml` / `config/` / `.sources` 时返回仓库根，避免 `No module named ai_rulec` 与本地 `config/agents.yaml` 被忽略；wheel 场景仍用 meta 所在目录。
- **Windows 文件锁偏移**：`FileLock.acquire()` 在 `msvcrt.locking` 前补 `seek(0)`，与 `release()` 的解锁位置一致；锁文件非空时不再锁错字节。
- **`watch --once --health` 输出补 `skipped` / `pending`**；`health --compare` 在代理关闭时返回 `compare_skipped: true` 并说明需要可监听代理。
- **`check_secrets.py` 扫描 annotated tag 的 `tagger`**；`v0.13.0` 与 `v0.16.1` 已用 GitHub noreply 身份重建，历史检查 0 命中。

### Docs

- `PROJECT.md` / `RELEASE.md` 同步 0.17.1 与 169 个用例；README 目录树补 `test_catalog.py` / `test_probe.py` / `test_check_secrets.py`，Agent 健康检查段补跳过契约；`service` 子命令按动作拆分选项；两个安装器的 `--no-ui` 均打印「已跳过」。

## [0.17.0] - 2026-11-23

按内部审查报告整改。三条 P0 都是真实的阻断问题，全部已修。

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
