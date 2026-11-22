# Security Policy

## 支持范围

当前支持 **0.13.x**（alpha）。更早版本不保证修复。

## 报告问题

发布到 GitHub 后，请使用仓库的 **Security → Report a vulnerability**（私密安全公告），不要开公开 issue 贴配置、日志、订阅地址或密钥。

在仓库还没有配置 remote/私密公告之前，报告时请先脱敏：删除节点、订阅 URL、token、Cookie、内网地址和个人路径。

## 在范围内

- `apply` / `doctor` / `rollback` / `watch` 对 profile、config、provider cache 的路径处理问题（路径穿越、写错文件、覆盖非目标文件）。
- `scripts/*.sh` 的命令注入、引号/转义错误、`launchctl` plist 注入。
- 生成物或日志中意外包含敏感信息。
- `fetch_sources.sh` 拉取来源时的供应链风险（未校验、写到非预期路径）。
- Hiddify/Surge/sing-box patch 把配置写到错误位置，或校验失败仍写入。

## 不在范围内

- 第三方节点、机场、订阅本身的安全。
- 第三方规则源的准确性、可用性、许可证合规（见 `docs/THIRD_PARTY.md`）。
- FlClash/mihomo/sing-box/Surge/Hiddify 自身的漏洞。
- 用户本机已被入侵、或用户主动把配置/密钥提交到仓库的情况。

## 安全默认

- 仓库不包含 `.sources/`、`out*/`、真实 profile、日志、密钥。
- 所有写入型命令都有 `--dry-run` 或副本验证路径；`apply`/`surge`/`singbox` 先校验再备份、再原子替换。
- 本项目不收集遥测，不主动上传配置；网络请求仅用于拉取来源、探针和健康检查（见 `PRIVACY.md`）。
- 发现泄露时，先轮换密钥，再清理历史；仅删除当前文件不够。
