# Privacy

ai-rulec 是本地优先工具，不做账号体系，不收集遥测。

## 会发起哪些网络请求

- `scripts/fetch_sources.sh`：从 GitHub / raw.githubusercontent.com 拉取规则源和 release 文件。
- `build --probe`：对服务域名做 DNS/TLS/HTTP 探测；`--probe-proxy auto` 时会经本机 mixed-port。
- `health` / `status --health`：对 `config/agents.yaml` 中的端点做请求，并可经本机代理。
- `verify`：调用本机 `mihomo`、`sing-box`、`surge-cli`，不主动上传配置。

这些请求会让目标站点看到你的出口 IP、User-Agent 和请求的域名；行为与浏览器/客户端直接访问类似。

## 会写哪些本地数据

- `.sources/`：上游仓库副本（已 gitignore）。
- `out*/`：catalog、coverage、规则产物、lock（已 gitignore）。
- FlClash/Hiddify/Surge 配置目录中的备份、`AI-Rules/`、watch state、日志。
- `~/Library/Logs/` 下的 LaunchAgent 日志。

日志可能包含本机路径、域名、状态码和错误文本；默认不记录 API key、Cookie、订阅 URL。分享日志前仍建议脱敏。

## 不会做什么

- 不收集遥测、不嵌入分析 SDK、不要求登录。
- 不上传 profile、规则产物或 agent 配置。
- 不读取 `config/agents.yaml` 之外的凭据；本项目不管理 API key。
- 不把 `.sources/`、`out*/` 提交到仓库。

## 第三方

规则来源、代理客户端和 AI 服务各有自己的隐私政策；本项目的网络请求会经过它们。详见 `docs/THIRD_PARTY.md`。
