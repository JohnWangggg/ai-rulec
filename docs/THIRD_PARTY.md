# 第三方来源与归属

本仓库只包含代码、配置和转换逻辑，不包含第三方规则数据，也不包含 `out*/` 生成物。构建时由 `scripts/fetch_sources.sh` 从下列上游拉取；每个来源的 commit/sha256 会写入 `out/lock.json`。

共 **7 个来源**，与 `sources.yaml`、`scripts/fetch_sources.sh`、`scripts/install.ps1` 以及 `ai_rulec/data/sources.yaml` 的内置默认完全一致：5 个整仓 `git clone --depth 1`（v2fly、jinxinkai、G4free、ma-wenqian、SukkaW），加 2 个单文件来源（blackmatrix7 的 6 个 AI 规则集、Loyalsoldier 的 `direct`/`proxy`/`reject`，合计 9 次下载）。`MetaCubeX/meta-rules-dat` **没有被使用**：它不在 `sources.yaml` 里，`fetch_sources.sh` 与 `install.ps1` 也都不拉取它。若以后要用 geosite 数据，需要先加来源、再重新做许可证评估。

| 来源 | 用途 | 许可证 | 分发注意 |
|---|---|---|---|
| [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) | 基础域名列表 | MIT | 保留版权/许可声明即可 |
| [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules) | 聚合/分流规则 | GPL-3.0 | 与 GPL-3.0 代码兼容；分发产物时保留许可证与源码获取方式 |
| [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) | AI 分类规则 | GPL-2.0 | GPL-2.0-only 不能当然升级到 GPL-3.0；对外分发前移除或取得授权 |
| [SukkaW/Surge](https://github.com/SukkaW/Surge) | AI 规则 | 仓库元数据未确认 | 使用/分发前读仓库 LICENSE |
| [jinxinkai/clash-ai-rules](https://github.com/jinxinkai/clash-ai-rules) | AI 手工规则 | README 称 MIT，仓库无 LICENSE 文件 | 以实际 LICENSE 为准；对外分发前确认 |
| [ma-wenqian/ai-proxy-rules](https://github.com/ma-wenqian/ai-proxy-rules) | AI 手工规则 | 仓库有 LICENSE，GitHub 未识别 | 读 LICENSE 原文后再分发 |
| [G4free/clash-ruleset](https://github.com/G4free/clash-ruleset) | AI 规则补充 | 无 LICENSE | 默认保留所有权利；不建议进入对外分发 |

## 代码依赖

运行时仅依赖 [PyYAML](https://pyyaml.org/)（MIT）。测试使用 Python 标准库 `unittest`。

## 生成物

`out/AI-Rules/*`、`out/singbox/*`、`out/surge/*`、`out/mihomo-*.yaml` 可能包含上述来源的实质性选择/编排。对外分发前请按 [LICENSE-ASSESSMENT.md](LICENSE-ASSESSMENT.md) 逐项确认；本文件不是法律意见。
