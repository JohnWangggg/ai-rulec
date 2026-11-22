# Contributing

## 开发环境

- Python 3.10+，运行时只依赖 PyYAML。
- macOS 上 FlClash/Surge/Hiddify 的集成测试会读写真实配置，默认不要在测试里做；用临时目录和 `--dry-run`。

```bash
python3 -m pip install PyYAML
./scripts/check.sh
```

## 改动原则

1. 先加/改测试，再改实现；不要用真实订阅、真实账号或真实代理配置做测试夹具。
2. 规则来源必须保留 provenance：`source`、`commit`/`sha256`、生成时间写入 `out/lock.json`。
3. 新增来源前先确认许可证；GPL-2.0-only、无 LICENSE、自定义许可证的来源不要进入默认分发路径。
4. 不要提交 `.sources/`、`out*/`、真实 profile、日志或密钥。
5. 涉及 FlClash/Surge/Hiddify 写入的改动必须提供 `--dry-run` 或副本验证，并在 PR 里写清回滚方式。

## 提交

- 小而聚焦的提交；提交信息说明“为什么”，不要只写“update”。
- PR 描述包含：改了什么、怎么验证、对现有 live 配置的影响、许可证影响（如有新来源）。
