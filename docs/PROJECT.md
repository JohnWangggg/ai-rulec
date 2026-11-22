# 项目名称与定位

## 名称

**ai-rulec**（仓库名、Python 包名、CLI 命令一致）。

`rulec` = rule compiler。保留这个名字，不为了品牌再改一次包名/入口；对外展示也用 `ai-rulec`，不另起别名。

## 一句话

**AI 服务规则的编译器与生命周期层。**

把「哪些 AI 服务、哪些域名/进程/ASN、走哪个出口」从手写 YAML 变成可编译、可验证、可回滚、可自愈的配置。

## 它解决什么

1. AI 服务域名变化快，手写 `DOMAIN-SUFFIX` 跟不上，且没人知道某条规则为什么在。
2. FlClash/mihomo 订阅刷新会覆盖 profile；provider cache 与 config 的 hashed 路径会不同步。
3. 规则语法正确不等于能用：出口组选错、ASN 过宽、共享域误伤、探针直连被墙，都会造成“配置没错但工具不通”。
4. 多客户端格式重复劳动：mihomo、sing-box/Hiddify、Surge 各写一遍。

## 它不做什么

- **不是节点/订阅提供商**：不卖节点，不生成节点。
- **不是账号/token 池**：不托管 API key，不做多账号调度；这些是 L2/L3。
- **不是 MITM/DLP 产品**：不默认解密流量，不做内容审查。
- **不是又一个规则库**：规则库是输入；本项目做目录、编译、验证和生命周期。
- **不替代客户端**：不接管 FlClash/Surge/Hiddify 的 UI、启动和节点选择。

## 目标用户

- 同时使用多个 AI CLI/客户端的开发者（Claude Code、Codex、Gemini CLI、Cursor、Windsurf、Antigravity、Muse 等）。
- 用 FlClash/mihomo 做主代理，但被订阅刷新、规则覆盖、缓存不同步困扰的人。
- 需要把 AI 分流规则落到 sing-box/Hiddify 或 Surge 的小团队。
- 想审计“这条规则从哪来、为什么留、什么时候失效”的安全/运维人员。

## 差异点

| 常见做法 | ai-rulec |
|---|---|
| 几千行平铺域名 | 服务目录 + 角色 + 来源 + 置信度 |
| 只生成规则文件 | 生成 + 校验 + 幂等 apply + rollback + watch |
| 各客户端各写一套 | 一份 catalog 编译 mihomo / sing-box / Surge |
| 规则正确就认为可用 | probe + agent health + 出口推断 |
| 订阅刷新后人工修 | 检测 profile 重写并自动重放 |

## 阶段

当前 **0.13.0 alpha**。已在 macOS + FlClash 上完成真实 apply、订阅重写恢复和流量验证；sing-box/Hiddify 已做 live 配置副本的端到端验证，Surge 已做 profile 副本验证。没有接真实节点池，也没有做 token/账号/agent 语义。

## 发布原则

- 代码 GPL-3.0-or-later；仓库不包含第三方规则数据或 `out*/` 生成物。
- 默认不收集遥测；所有配置和日志留在本机。
- 新来源先过许可证审查；GPL-2.0-only、无 LICENSE、自定义许可证不进入默认分发路径。
