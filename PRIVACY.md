# Privacy

ai-rulec 是本地优先工具，不做账号体系，不收集遥测。

## 会发起哪些网络请求

- `scripts/fetch_sources.sh`（macOS / Linux）：从 GitHub / raw.githubusercontent.com 拉取规则源和 release 文件。
- `scripts/install.ps1`（Windows）：同样拉取 5 个仓库（`git clone --depth 1`，需要 Git for Windows）和 9 个单文件（`Invoke-WebRequest`），只是不经过 bash 脚本。
- `build --probe` / `probe`：对服务域名做 DNS/TLS/HTTP 探测；`--probe-proxy auto`（默认）时会经本机 mixed-port。
- `health` / `status --health`：对 `config/agents.yaml` 中的端点做请求，并可经本机代理。
- `verify`：调用本机 `mihomo`、`sing-box`、`surge-cli`，不主动上传配置。
- `ui`：只绑定 `127.0.0.1`（可用 `--port` 换端口），每次启动生成随机 token 并作为 URL 参数打印到终端；token 不写盘，也不对外暴露。

这些请求会让目标站点看到你的出口 IP、User-Agent 和请求的域名；行为与浏览器/客户端直接访问类似。

## 会写哪些本地数据

- `.sources/`：上游仓库副本（已 gitignore）。
- `out*/`：catalog、coverage、规则产物、lock（已 gitignore）。
- FlClash/Hiddify/Surge 配置目录中的备份、`AI-Rules/`、watch state、日志。
- **状态目录**（`AI_RULEC_STATE` 可覆盖，否则 `ai-rulec/` 位于平台状态根下）：
  - macOS：`~/.local/state/ai-rulec/`（遵循 `XDG_STATE_HOME`）
  - Linux：`~/.local/state/ai-rulec/`
  - Windows：`%LOCALAPPDATA%\ai-rulec\`
  里面有 `last-build.json`（最近一次构建的 `out` 路径与元信息）、watch 的 state/events 等。
- **日志**：
  - macOS：`~/Library/Logs/ai-rulec-watch.log`、`~/Library/Logs/ai-rulec-watch.err.log`（`service` 装的 LaunchAgent）
  - Linux：`~/.local/state/ai-rulec/log/ai-rulec-watch.log`、`ai-rulec-watch.err.log`（systemd user unit）
  - Windows：`%LOCALAPPDATA%\ai-rulec\logs\` 是路径约定，但计划任务会丢弃 stdout/stderr，所以 `service install` 装的任务**不产生持久日志**；实时状态用 `ai-rulec service status`。前台手动跑 `watch` 时输出只到终端，除非你自己重定向。

日志可能包含本机路径、域名、状态码和错误文本；默认不记录 API key、Cookie、订阅 URL。分享日志前仍建议脱敏。

## 不会做什么

- 不收集遥测、不嵌入分析 SDK、不要求登录。
- 不上传 profile、规则产物或 agent 配置。
- 不读取 `config/agents.yaml` 之外的凭据；本项目不管理 API key。
- 不把 `.sources/`、`out*/` 提交到仓库。

## 第三方

规则来源、代理客户端和 AI 服务各有自己的隐私政策；本项目的网络请求会经过它们。详见 `docs/THIRD_PARTY.md`。
