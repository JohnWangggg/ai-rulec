# Changelog

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
