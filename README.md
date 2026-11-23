# ai-rulec

把 AI 服务规则从「手工域名清单」编译成**可验证、可回滚、可自愈**的代理配置。

它不是又一个 Clash 规则库，而是规则库上面的编译与生命周期层：

- **服务目录**：按服务、角色、来源、置信度组织域名/进程/ASN，而不是几千行平铺 `DOMAIN-SUFFIX`。
- **严格发射**：默认剔除共享域、未知服务、过宽 ASN、reject 集合；保留的每条规则都能回答「为什么在」。
- **FlClash/mihomo 集成**：生成每服务代理组 `AI-<service>`，把规则插到用户规则之前，并维护 hashed provider cache。
- **生命周期**：`doctor` 修缓存，`apply` 幂等合并，`watch` 在 FlClash 从订阅重写 profile 后自动重放，`rollback` 恢复备份。
- **可验证**：mihomo `-t`、探针、单元测试、CI；失败不写半成品。
- **多客户端**：同一份 catalog 编译成 mihomo YAML、sing-box rule-set（`version 3`）、Surge `.list`；`build --formats all` 一次出齐，`verify` 会用 `sing-box check` / `surge-cli --check` 实跑校验。
- **Agent 健康**：内置 7 个 AI 工具的端点/CLI 检查，`status` 一并汇总；FlClash 自带的 13 项检测不可配置，这层外挂在它之上。

当前是 alpha，已在 macOS + FlClash 上做过真实 apply 和流量验证，在三格式产物上跑过 mihomo/sing-box/Surge 各自的校验；没有接真实节点池，也没有做 token/账号/agent 语义（那是下一层）。

许可证：**GPL-3.0-or-later**。仓库不包含第三方规则数据或 `out*/` 生成物；来源与分发注意事项见 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)。

## 系统支持

双击仓库根目录的 `install.command`。它会：建 venv → 装 mihomo（有 brew 时）→ 拉来源 → 编译 → 应用到当前客户端 → 装 LaunchAgent → 打开本地控制台。中途失败会停在 Terminal 里显示报错。

```bash
./install.command                 # 完整安装
./install.command --no-agent      # 不装后台看护
./install.command --skip-probe    # 跳过端点探测，构建更快
```

三个平台的产物位置与命令行为一致，差异只在目录约定和后台服务管理器；目录表见 [docs/PLATFORMS.md](docs/PLATFORMS.md)。

## 本地控制台

```bash
ai-rulec ui          # 打开 http://127.0.0.1:8787/?token=...
ai-rulec ui --port 9000 --no-open
```

页面显示 AI 组出口、provider 缓存、代理端口、后台看护和 agent 连通性；四个按钮对应 `apply` / `doctor --fix` / `rollback` / `build`。服务只绑 loopback，每次启动生成一次性 token，跨站请求会被拒绝。

## 快速开始

两种用法：仓库内直接跑（`python3 -m ai_rulec ...`），或用 pip 装（`ai-rulec ...`）。配置默认随包分发（`ai_rulec/data/*.yaml`），装完即可用；仓库根目录的 `config/services.yaml`、`config/agents.yaml`、`sources.yaml` 存在时会优先于内置默认值。

```bash
# 0. 可选：装成命令。wheel 内置默认配置，不需要仓库
python3 -m pip install ai-rulec
ai-rulec health --list          # 不依赖仓库路径

# 1. 依赖
python3 -m pip install pyyaml

# 2. 拉取来源（git clone + release 文件，约 20MB）
./scripts/fetch_sources.sh
# 装成包、不在仓库目录时用 SOURCE_ROOT 指向来源目录
export SOURCE_ROOT="$PWD/.sources"

# 3. 构建。IP-ASN 需要 ASN.mmdb；没有就 drop，避免 mihomo 启动时下载失败
#    探针默认走本机代理（proxy auto），否则 CN 直连会把可用的 AI 域名判成 tls_fail
ai-rulec build --asn-mode drop --probe --probe-proxy auto

# 一次出齐 mihomo / sing-box / Surge 三套产物
ai-rulec build --asn-mode drop --probe --probe-proxy auto --formats all

# 4. 检查当前 FlClash profile 和 provider cache
ai-rulec discover
ai-rulec status --latest --health
ai-rulec doctor --latest

# 5. 先 dry-run，再真实合并。省略 --target 时自动推断出口组
#    （url-test/fallback 优先，其次「手动选择」这类 selector）
#    从任意目录都能跑：省略 --meta 时用最近一次 build 的输出
ai-rulec apply --latest --dry-run
ai-rulec apply --latest

# 可选：单个服务指定出口
ai-rulec apply --latest --map openai=手动选择

# 6. 单次 reconcile（CI/调试）或常驻
ai-rulec watch --latest --once
ai-rulec watch --latest --interval 5 --health
```

FlClash 从订阅重写 profile 后，`AI-*` 组会消失；`watch` 会在下一轮检测到并自动重放。这是本项目存在的主要理由。

## 后台服务（7 天 soak）

三个平台用同一个子命令，服务管理器按系统自动选择：macOS 用 LaunchAgent，Linux 用 systemd user unit，Windows 用当前用户的计划任务。

```bash
ai-rulec service install --interval 5   # 注册并启动常驻 watch
ai-rulec service status                 # 是否在跑 + 最近日志
ai-rulec service describe               # 只看会生成什么服务定义
ai-rulec service uninstall              # 停止并移除
```

日志：

- `~/Library/Logs/ai-rulec-watch.log`
- `~/Library/Logs/ai-rulec-watch.err.log`

后台服务只负责规则/cache 的生命周期和健康巡检，不会替你点 FlClash 的启动按钮；代理未启动时 `status` 会报 `proxy port ... is not listening`。三个平台的安装器都只调用 `ai-rulec service install`，不再各自写 plist / unit / 计划任务。

## 命令

| 命令 | 作用 | 关键安全开关 |
|---|---|---|
| `build` | 生成 catalog、coverage、每服务 provider、mihomo 配置 | `--asn-mode keep\|drop`、`--raw`、`--probe-proxy` |
| `discover` | 找 FlClash profile/config，标记是否已管理 | `--root`、`--json` |
| `status` | 一次性看 profile/缓存/端口/watch/agent 健康 | `--health`、`--json` |
| `doctor` | 检查并可选修复 hashed provider cache | `--fix`、`--dry-run` |
| `apply` | 合并进真实 profile，备份、拷贝 provider、mihomo 验证、原子写 | `--dry-run`、`--rules-last`、`--reset-proxies`、`--no-repair-cache` |
| `watch` | 轮询 config/profile，重放丢失的 AI 组、修 cache、周期健康检查 | `--once`、`--no-reapply`、`--no-fix-cache`、`--health` |
| `rollback` | 恢复 apply 前的 profile/AI-Rules | `--list`、`--dry-run` |
| `merge` | 只合并 base + meta，不碰 FlClash 数据目录 | `--target`、`--map` |
| `surge` | 把 AI 组/规则补丁进现有 Surge profile，`surge-cli --check` 通过后才原子写 | `--dry-run`、`--rules-last`、`--no-backup` |
| `singbox` | 把 AI selector/rule-set 补丁进现有 sing-box/Hiddify config | `--dry-run`、`--map`、`--absolute-paths`、`--no-backup` |
| `singbox-watch` | 订阅刷新后自动重打 Hiddify/sing-box 补丁 | `--once`、`--dry-run`、`--force`、`--interval` |
| `health` | 检查 Antigravity/Muse/Claude Code/Codex 等 agent 的 HTTP 端点与本地 CLI | `--proxy auto\|direct\|URL`、`--compare`、`--agent` |
| `service` | 注册/查询/移除后台 watch（launchd / systemd user / 计划任务） | `--dry-run`、`--json`、`--interval` |
| `ui` | 本地 Web 控制台，loopback + 一次性 token | `--port`、`--no-open` |

`build --formats` 取值：

| 值 | 产物 | 消费方 |
|---|---|---|
| `mihomo`（默认） | `out/AI-Rules/*.yaml`、`out/mihomo-meta.yaml`、`out/mihomo-config.yaml` | mihomo / FlClash / Clash Verge |
| `sing-box` | `out/singbox/AI-Rules/*.json`（rule-set v3）、`out/singbox/config.json` | sing-box / Hiddify / SFA |
| `surge` | `out/surge/AI-Rules/*.list`、`out/surge/fragment.conf`、`out/surge/profile.conf` | Surge（`.list` 可直接被 `RULE-SET,路径,策略` 引用） |
| `all` | 以上全部 | — |

sing-box 没有 ASN 匹配器，`ip_asn` 会被丢弃（`asn_mode` 对 sing-box 无效）；Surge 的 `.list` 保留 `IP-ASN`。

`apply` 默认：

- 规则插到用户 `MATCH` 之前，覆盖原有 `Ai+` 等分组；
- 已有 `AI-*` 组幂等替换，未显式 mapping 时保留上次出口；
- 备份 profile 和 `AI-Rules`；
- 只在 mihomo 验证通过后原子替换；
- 拒绝把 target/mapping 指到 `AI`/`AI-*`，避免自引用。

## 其它客户端

| 客户端 | 子命令 | 写入方式 | 状态 |
|---|---|---|---|
| mihomo / FlClash | `apply`、`watch`、`doctor` | 直接改 profile + hashed provider cache，`mihomo -t` 通过后原子写 | 实机已验证（macOS + FlClash） |
| sing-box / Hiddify | `singbox`、`singbox-watch` | 补丁 `route.rules` 与 `AI-*` selector，`sing-box check` 通过后原子写 | sing-box 已支持；Hiddify 实机写入**未启用**（未在真实 Hiddify 上验证，先用 `--dry-run`） |
| Surge | `surge` | 定点 patch `[Proxy Group]`/`[Rule]`，`.list` 复制到 profile 同目录，`surge-cli --check` 通过后原子写 | 已支持（需本机 `surge-cli`） |

### Surge

Surge 没有 provider 文件热插拔：`[Proxy Group]` 和 `[Rule]` 必须写在 profile 里，`.sgmodule` 也不能定义组。因此 `surge` 子命令直接改 profile：

```bash
python3 -m ai_rulec build --formats surge --asn-mode keep
python3 -m ai_rulec surge --dry-run          # 自动找 ~/Library/Application Support/Surge/Profiles
python3 -m ai_rulec surge                    # 备份 + surge-cli --check + 原子写
python3 -m ai_rulec surge --rules-last       # AI 规则放到 FINAL 前，用户规则优先
python3 -m ai_rulec surge --map openai=AIGC  # 单服务指定已有策略
```

行为：

- 目标策略默认从 profile 的 `select` 组推断（`Proxy` / `AIGC` / 第一个含 `DIRECT` 的组）；
- 只替换 `AI`/`AI-<service>` 开头的组和指向它们的 `RULE-SET`，其余原样保留；
- `.list` 复制到 profile 同目录的 `AI-Rules/`，`RULE-SET,AI-Rules/<service>.list,AI-<service>` 用相对路径，Surge 相对 profile 解析；
- 先写 `.ai-rulec-preview-*.conf` 跑 `surge-cli --check`，不通过就丢弃，绝不写坏 profile；
- 打补丁前先校验原始 profile：像 `云梯.conf` 那种靠远端 `#!include` 拉节点的配置，在 Surge 外必然报 `关联配置未就绪`，命令会拒绝盲写并提示先解决订阅；
- 备份为 `<profile>.bak.<YYYYmmdd-HHMMSS>`。

### sing-box

```bash
python3 -m ai_rulec build --formats sing-box --asn-mode drop
sing-box check -D out/singbox -c out/singbox/config.json
```

`out/singbox/config.json` 是可直接跑的骨架（mixed 入站 + `direct` 出站 + `AI-*` selector + rule-set 路由），需要真实节点时替换 `outbounds`。rule-set 是 `version 3` 源格式，`sing-box rule-set compile` 可再压成 `.srs`。

Hiddify 的配置是 sing-box fork，订阅刷新会重写 `current-config.json`。`singbox` 子命令直接给现有配置打补丁：

```bash
python3 -m ai_rulec singbox --dry-run   # 自动找 ~/Library/Application Support/app.hiddify.com/data/current-config.json
python3 -m ai_rulec singbox             # 备份 + sing-box check + 原子写
python3 -m ai_rulec singbox --target select --map openai=select
```

行为：

- 插入 `AI-<service>` selector 和父组 `AI`，并把 `AI-Rules/<service>.json` 复制到 config 同目录；
- 路由规则插到 `route.rules` 顶部，`route.final` 缺省时补成目标出站；
- Hiddify fork 的 `balancer`、`tolerance`、`tls_fragment` 等上游 sing-box 不认识的字段，只在临时校验副本里替换/删除，原文件保留；
- 校验通过才备份并原子替换；`--dry-run` 不写任何文件。

Hiddify 更新订阅会重写 `current-config.json`，可以挂一个 watcher 自动重打：

```bash
python3 -m ai_rulec singbox-watch --once --dry-run   # 先看会不会重打
python3 -m ai_rulec singbox-watch --interval 5       # 常驻，检测到 AI 组/rule-set 丢失才重打
```

watcher 用 `<config dir>/.ai-rulec-singbox-watch.json` 记录状态、用系统临时目录的 lock 文件防并发；配置没变或 AI 组完整时是 no-op。写入后 Hiddify 仍需重载/重启才会使用新配置。

## Agent 健康检查

FlClash 自带的服务检测是 Go 里硬编码的 Google/GitHub/ChatGPT 等 13 项，新增服务要改 Go + Flutter 并重新打包。`health` 是外置的配置驱动版本，不改 FlClash 二进制：

```bash
python3 -m ai_rulec health --list
python3 -m ai_rulec health --proxy auto
python3 -m ai_rulec health --compare   # 同时跑代理/直连，区分 proxy 断与上游断
```

`config/agents.yaml` 里每个 agent 由 `checks` 组成：

- `http`：URL + `expect` 状态码；`required: false` 失败只降级，不判 down。
- `binary`：本地命令 + `--version`；未安装且 optional 时跳过。
- `--compare` 会复用同一组检查跑直连，输出 `proxy_route`、`proxy_down`、`upstream` 等诊断。

- 传输类错误（timeout / TLS / network）自动重试，最多 3 次；HTTP 状态码不重试。`retries: 0` 可关闭。

状态：`ok` / `degraded`（可选检查失败）/ `down`（必选检查失败）。它证明端点可达和本地 CLI 存在，不等价于完整 agent 功能测试。

`status` 可直接带健康检查，一条命令看全链路：

```bash
python3 -m ai_rulec status --latest --health
# agents:  7 ok, 0 degraded, 0 down  via http://127.0.0.1:7890
```

代理地址默认从 profile/`config.yaml` 的 `mixed-port` 推断；推断不到时才用 `7890`。

`watch --health` 会把健康结果按 `--agents-interval`（默认 300s）写进 state，供 `status` 回看。

## 目录

```text
ai_rulec/
  sources.py    # 解析 Clash/Surge/V2Ray/YAML，两阶段过滤
  catalog.py    # 归属、角色、flags、coverage、probe 聚合
  emit.py       # 严格发射、mihomo/sing-box/Surge 产物、lock
  formats.py    # sing-box rule-set 与 Surge list/patch 的文本生成
  surge.py      # Surge profile 发现、备份、校验、原子写
  singbox.py    # sing-box/Hiddify config patch、fork 字段清洗、校验
  singbox_watch.py  # 订阅刷新后自动重打 Hiddify 补丁
  probe.py      # DNS/TLS/HTTP 探针，DoH 回退，支持走代理
  merge.py      # base + meta 合并，幂等、规则顺序
  profile.py    # discover/apply/doctor/repair/rollback
  watch.py      # reconcile 状态机与文件锁
  status.py     # 只读状态
  health.py     # agent HTTP/binary 健康检查
  defaults.py   # 内置默认配置解析（内置包 -> 本地覆盖）
  paths.py      # 最近一次 build 的输出、跨目录 --meta、mihomo 定位
  ui.py         # 本地 Web 控制台（loopback + token）
  cli.py
install.command               # macOS 双击安装器
  PROJECT.md    # 定位、范围、目标用户
  RELEASE.md    # 发布清单
  THIRD_PARTY.md  # 来源与归属
  LICENSE-ASSESSMENT.md  # 许可证策略
  TRADEMARKS.md  # 商标声明
ai_rulec/data/services.yaml   # 内置默认：11 个服务的 known_domains/processes
ai_rulec/data/agents.yaml     # 内置默认：agent -> service/checks
ai_rulec/data/sources.yaml    # 内置默认：7 个来源
config/services.yaml          # 本地覆盖（可选，优先于内置）
config/agents.yaml            # 本地覆盖（可选）
sources.yaml                  # 本地覆盖（可选）
scripts/fetch_sources.sh
scripts/install_launchagent.sh
scripts/install_singbox_agent.sh
scripts/check_secrets.py     # 仓库/历史敏感信息扫描
tests/test_core.py
tests/test_health.py
tests/test_health_retry.py
tests/test_formats.py
tests/test_singbox.py
tests/test_singbox_watch.py
tests/test_singbox_agent_script.py
tests/test_watch_status.py
tests/test_client_discovery.py
tests/test_defaults.py
```

产物：

- `out/catalog.json`、`out/coverage.json`、`out/lock.json`（lock 记录 `formats`、来源 SHA、产物 hash）
- mihomo：`out/AI-Rules/<service>.yaml`、`*.domain.yaml`、`out/mihomo-meta.yaml`、`out/mihomo-config.yaml`
- sing-box：`out/singbox/AI-Rules/<service>.json`、`out/singbox/config.json`
- Surge：`out/surge/AI-Rules/<service>.list`、`out/surge/fragment.conf`、`out/surge/profile.conf`

## 来源与许可证

本项目代码采用 **GPL-3.0-or-later**，见 [LICENSE](LICENSE)。选择 GPL 是为了和 mihomo/Clash 生态及 GPL-3.0 规则源保持兼容；你可以按 GPL 使用、修改、分发，包括商业使用，但没有担保。

仓库**不包含**第三方规则数据或生成物：`.sources/`、`out*/` 都在 `.gitignore` 中。`sources.yaml` 只记录上游地址和转换规则，构建时由你本地拉取。对外分发 `out*/` 产物前，请单独确认每个来源的许可证：

- v2fly/domain-list-community：MIT，最安全。
- Loyalsoldier/clash-rules、MetaCubeX/meta-rules-dat：GPL-3.0，与本项目代码兼容。
- blackmatrix7/ios_rule_script：GPL-2.0，需确认是否允许升级到 GPL-3.0。
- SukkaW/Surge、jinxinkai/clash-ai-rules、ma-wenqian/ai-proxy-rules：仓库元数据未给出明确/标准许可证，分发前逐项核对。
- G4free/clash-ruleset：无 LICENSE，默认保留所有权利，不建议进入对外分发。

详见 [docs/LICENSE-ASSESSMENT.md](docs/LICENSE-ASSESSMENT.md) 与 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)。这不是法律意见。

## 已知限制

- `apply`/`doctor`/`watch` 的 FlClash 路径发现是 macOS 优先；Linux/Windows 需要补数据目录。
- `watch` 不自动点 FlClash 的启动按钮，也不替代 FlClash 的 UI；它只保证规则和 cache 不漂移。
- 探针只做 DNS/TLS/HTTP 可达性，不做账号/区域/模型级验证。
- `mihomo -t` 只验证配置可加载，不验证实际路由语义。
- 真实出口由 `apply` 从 profile 推断（url-test/fallback 优先，其次 selector 组）；只有推断不出时才退回 `DIRECT`。
- FlClash 把 provider 改写成 hashed 绝对路径；重建后缓存比较按 payload 而非整文件，故 `# generated:` 时间戳变化不会误报 mismatch。
- `sing-box check` 不校验 outbound/rule_set 标签引用，`surge-cli --check` 不校验外部 `.list` 是否存在；两处都只能证明语法，语义靠单元测试和运行时冒烟。
- Surge 的 `.sgmodule` 不能定义策略组，所以 Surge 集成必须改 profile；`surge` 子命令只做定点 patch，不合并节点订阅。
- 没有接真实节点池、token 计量、账号池、MCP 语义；这些属于 L2/L3。

## 开发与测试

只有 PyYAML 一个运行时依赖，测试用标准库 `unittest`，不需要 pytest：

```bash
python3 -m pip install PyYAML
python3 scripts/check_secrets.py --history              # 敏感信息扫描（CI 同款）
python3 -m compileall -q ai_rulec                      # 语法检查（CI 同款）
python3 -m unittest discover -s tests -v               # 全部单元测试
./scripts/check.sh                                     # 上面两步的封装
python3 -m pip wheel . --no-deps -w /tmp/ai-rulec-wheel  # 验证可打包出 wheel
python3 -m ai_rulec --help                             # 验证 CLI 入口可用
```

CI 在 `.github/workflows/ci.yml`：push / PR / 手动 `workflow_dispatch` 触发，Python 3.11，依次跑敏感信息扫描、`compileall`、`unittest discover`。

## 项目文档

- [docs/PROJECT.md](docs/PROJECT.md)：名称、定位、范围、目标用户、差异点
- [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)：来源与归属
- [docs/LICENSE-ASSESSMENT.md](docs/LICENSE-ASSESSMENT.md)：许可证策略
- [docs/TRADEMARKS.md](docs/TRADEMARKS.md)：商标声明
- [docs/RELEASE.md](docs/RELEASE.md)：发布清单
- [SECURITY.md](SECURITY.md)、[PRIVACY.md](PRIVACY.md)：安全与隐私
- [CONTRIBUTING.md](CONTRIBUTING.md)、[CHANGELOG.md](CHANGELOG.md)
