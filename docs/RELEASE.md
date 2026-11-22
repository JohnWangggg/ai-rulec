# 发布清单

发布前逐项确认；每项都要有可复现的命令或证据，不靠记忆。

## 1. 名称与定位

- [x] 名称：`ai-rulec`（PyPI / GitHub / CLI 三者一致；PyPI `ai-rulec` 未被占用）
- [x] 定位：AI 服务规则的编译器与生命周期层（见 [PROJECT.md](PROJECT.md)）
- [x] 边界写清：不是节点商、不是 key 池、不是 MITM 产品、不是规则库

## 2. 文档

- [x] `README.md`：定位、快速开始（仓库内 + pip 安装两种）、命令表、客户端矩阵、已知限制、开发与测试
- [x] `docs/PROJECT.md`：名称、定位、不做什么、目标用户、差异点
- [x] `docs/THIRD_PARTY.md`：8 个来源 + PyYAML 的归属与分发注意
- [x] `docs/LICENSE-ASSESSMENT.md`：许可证策略与逐来源结论
- [x] `docs/TRADEMARKS.md`：商标声明
- [x] `SECURITY.md`、`PRIVACY.md`
- [x] `CONTRIBUTING.md`、`CHANGELOG.md`
- [x] `LICENSE`：GPL-3.0 全文，`pyproject.toml` 用 SPDX `GPL-3.0-or-later`

## 3. 敏感数据

```bash
python3 scripts/check_secrets.py --history     # 必须 0 命中
git status --short                             # 不应出现 .sources/、out*/、profile、日志
```

- [x] 跟踪文件扫描：0 命中
- [x] git 历史扫描：0 命中
- [x] 无 `.env`/`.pem`/`.key`/`.p12`/`id_rsa`/credential 文件
- [x] 无绝对家目录路径、个人邮箱、订阅 URI、真实 profile id（测试夹具已中性化）
- [x] `.sources/`（~19MB）与 `out*/` 在 `.gitignore`
- [x] CI 内置该扫描，防止回归

## 4. 打包与可安装性

```bash
python3 -m pip wheel . --no-deps -w /tmp/wheel
python3 -m venv /tmp/venv && /tmp/venv/bin/pip install /tmp/wheel/*.whl
cd /tmp && /tmp/venv/bin/ai-rulec health --list
```

- [x] wheel 构建通过（`ai_rulec-0.13.0-py3-none-any.whl`）
- [x] 元数据含 `License-Expression`、`Project-URL`、`License-File`
- [x] 内置 `ai_rulec/data/{services,agents,sources}.yaml`，脱离仓库可运行
- [x] 仓库外的 `build` 给出可操作的 `SOURCE_ROOT` 提示（不是 traceback）

## 5. 测试与校验

```bash
./scripts/check.sh          # compileall + unittest
```

- [x] 77 个单元测试通过
- [x] 真实 FlClash：29 组 / 161 规则 / 60 providers、cache 22/22、`status --health` 7/7
- [x] sing-box / Hiddify、Surge 在配置副本上端到端验证

## 6. 声明

- [x] 代码 GPL-3.0-or-later；仓库不含第三方数据与生成物
- [x] 无遥测、无账号、无上传
- [x] 商标：不隶属、未获背书；名称仅用于兼容性描述
- [x] 安全：私密报告渠道、支持范围、范围内/外分类
- [x] 免责：alpha；不担保；网络/出口/许可证由使用者自行确认

## 7. 发布动作（未执行，需人工确认）

- [ ] `git tag v0.13.0` 推送到远端
- [ ] GitHub Releases 附 wheel 与源码包
- [ ] PyPI 上传（`twine upload`）—— 发布前再跑一次第 3、4 节
- [ ] 仓库开启 Private vulnerability reporting
