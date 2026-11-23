# 许可证评估

这不是法律意见。结论用于工程决策，正式分发前应由法务确认。

## 两类对象

1. **代码**：`ai_rulec/`、脚本、测试。已采用 **GPL-3.0-or-later**，见仓库根目录 `LICENSE`。
2. **规则数据与派生产物**：`.sources/`、`out/AI-Rules/*`、`out/mihomo-*.yaml`、`out/lock.json`。这些来自第三方仓库，许可证各不相同。

代码许可证不能自动覆盖数据；数据许可证也不会因为“只是转换格式”而当然消失。本仓库只分发代码和配置，不 vendoring 第三方数据或生成物。

## 来源许可证

`ai_rulec/data/sources.yaml` 内置、`scripts/fetch_sources.sh` 与 `scripts/install.ps1` 实际拉取的都是下面 **7 个来源**，没有别的。`MetaCubeX/meta-rules-dat` 常被误列进名单，但它既不在 `sources.yaml` 里，也没有任何拉取脚本下载它；要引入需先加来源并重新评估。

| 来源 | 许可证 | 风险 | 备注 |
|---|---|---|---|
| v2fly/domain-list-community | MIT | 低 | 保留版权/许可声明即可 |
| Loyalsoldier/clash-rules | GPL-3.0 | 高 | 聚合/分发派生产物可能触发 GPL |
| blackmatrix7/ios_rule_script | GPL-2.0 | 高 | 同上；仅 GPL-2.0 |
| SukkaW/Surge | 未在仓库元数据中确认 | 中 | 使用前单独核对 |
| jinxinkai/clash-ai-rules | README 称 MIT，仓库无 LICENSE | 中 | 以实际 LICENSE 为准 |
| ma-wenqian/ai-proxy-rules | 自定义/未识别 | 中 | 需读 LICENSE 原文 |
| G4free/clash-ruleset | 无 LICENSE | 高 | 默认保留所有权利，不建议入库 |

## 已采用策略

1. **代码 GPL-3.0-or-later**：与 GPL-3.0 的 mihomo/规则生态兼容；允许商业使用、修改、分发，但分发时必须提供对应源码并保留许可证/版权声明。没有担保。
2. **数据不进仓库**：`.sources/` 已在 `.gitignore`；`fetch_sources.sh` 在构建时从上游拉取。仓库只保留来源元数据、转换代码和测试。
3. **产物不进仓库**：`out*/` 也不入库。对外分发 `out*/` 前，按来源重新评估；不要默认“生成物是我的”。
4. **GPL-2.0-only 需单独处理**：blackmatrix7/ios_rule_script 的 GPL-2.0 不能当然升级到 GPL-3.0。若要对外分发包含它的派生产物，先从默认来源移除，或取得授权。
5. **无许可证/自定义许可证不默认分发**：G4free 无 LICENSE；Sukka、jinx、ma-wenqian 的元数据不标准。保留为本地可选来源，分发前逐项核对。
6. **保留 provenance**：每个 provider 的 source、commit、sha256 已写入 `lock.json`；这是许可证审计和故障回滚的基础。
7. **不要只改格式就宣称避开 GPL**：域名集合的实质性选择/编排可能构成衍生；让法务看具体产物，而不是看脚本。
8. **公开产物有独立开关**：`sources.yaml` 每个来源带 `license` 与 `redistributable`；`build --redistributable-only` 只保留 `redistributable: true` 的来源，`lock.json` 记录 `redistributable_only`、`excluded_sources` 与每个来源的 `license`。GitHub Actions 的 `publish-tower` 只用这个模式发布 Tower 产物（当前仅 MIT 的 v2fly）。要放宽，先逐来源核对许可证并改这里的评估，不要直接改工作流。
