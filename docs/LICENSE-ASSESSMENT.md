# 许可证评估

这不是法律意见。结论用于工程决策，正式分发前应由法务确认。

## 两类对象

1. **代码**：`ai_rulec/`、脚本、测试。当前仓库没有 LICENSE 文件，默认是“保留所有权利”。
2. **规则数据与派生产物**：`.sources/`、`out/AI-Rules/*`、`out/mihomo-*.yaml`、`out/lock.json`。这些来自第三方仓库，许可证各不相同。

代码许可证不能自动覆盖数据；数据许可证也不会因为“只是转换格式”而当然消失。

## 来源许可证

| 来源 | 许可证 | 风险 | 备注 |
|---|---|---|---|
| v2fly/domain-list-community | MIT | 低 | 保留版权/许可声明即可 |
| Loyalsoldier/clash-rules | GPL-3.0 | 高 | 聚合/分发派生产物可能触发 GPL |
| blackmatrix7/ios_rule_script | GPL-2.0 | 高 | 同上；仅 GPL-2.0 |
| MetaCubeX/meta-rules-dat | GPL-3.0 | 高 | 二进制/文本产物都可能被视为衍生 |
| SukkaW/Surge | 未在仓库元数据中确认 | 中 | 使用前单独核对 |
| jinxinkai/clash-ai-rules | README 称 MIT，仓库无 LICENSE | 中 | 以实际 LICENSE 为准 |
| ma-wenqian/ai-proxy-rules | 自定义/未识别 | 中 | 需读 LICENSE 原文 |
| G4free/clash-ruleset | 无 LICENSE | 高 | 默认保留所有权利，不建议入库 |

## 建议

1. **代码许可证先不定**，等数据策略确定后再选。若只做 permissive 代码层，MIT 或 Apache-2.0 都比 GPL 更适合商业分发；但代码许可证救不了 GPL 数据。
2. **来源不进仓库**：`.sources/` 已在 `.gitignore`；`fetch_sources.sh` 在构建时拉取，仓库只保留来源元数据和转换代码。
3. **产物分开**：`out*/` 也不入库；对外分发前按来源重新评估。不要默认“生成物是我的”。
4. **商业/闭源分发**：优先 MIT/CC0/自有数据；GPL 来源单独构建、单独分发，或取得授权。保留 `lock.json` 的 commit/sha256，便于替换来源。
5. **保留 provenance**：每个 provider 的 source、commit、sha256 已写入 lock；这是许可证审计和故障回滚的基础。
6. **不要只改格式就宣称避开 GPL**：域名集合的实质性选择/编排可能构成衍生；让法务看具体产物，而不是看脚本。
