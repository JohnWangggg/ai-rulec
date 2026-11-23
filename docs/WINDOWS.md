# Windows 安装与运维

Windows 走和 macOS / Linux 相同的顺序：建 venv → 装依赖 → 检查 mihomo → 拉来源 → 编译 → 应用 → 注册后台服务 → 打开控制台。差别在服务管理器（当前用户的 Task Scheduler 任务）和目录约定（`%APPDATA%` / `%LOCALAPPDATA%`）。

> 当前状态：**已实现，未在真机验证**。脚本语法只做过静态检查（见文末「已知缺口」），先在 `-DryRun` 下看一遍计划，再小步真实写入。逐项矩阵见 [PLATFORMS.md](PLATFORMS.md)。

## 支持范围

| 系统 | 要求 |
|---|---|
| Windows 10 1809+ / Windows 11 | PowerShell **5.1**（系统自带即可，不需要 PowerShell 7） |
| 任意版本 | Python **>= 3.10**（[python.org 安装包](https://www.python.org/downloads/windows/) 或 `py` 启动器） |
| 拉取规则源时 | [Git for Windows](https://git-scm.com/download/win)（`scripts/install.ps1` 用 `git clone`） |

`scripts/fetch_sources.sh` 是 bash 脚本，Windows 上不由安装器调用。安装器自己做同一件事：

- 5 个仓库用 `git clone --depth 1` 拉到 `.sources/<名字>/`；
- 9 个单文件（blackmatrix7 的 6 个 AI 规则集、Loyalsoldier 的 `direct` / `proxy` / `reject`）用 `Invoke-WebRequest` 下载到 `.sources/bm7/` 与 `.sources/loyalsoldier/`。

每条来源**逐个**判断是否存在，已存在就跳过。所以中断后重跑只会补齐缺的部分，修好的 `.sources` 不会被重新拉一遍。如果你已经在 Git Bash 里跑过 `./scripts/fetch_sources.sh`，安装器会发现 `.sources` 齐全，一个网络请求都不发。

不想装 Git for Windows 也行：在 Git Bash 里跑一次 `./scripts/fetch_sources.sh` 再回来跑安装器。两条路都没走时，安装器会在第 4 步停下，并列出还缺哪几个目录。

## 安装

在仓库根目录（含 `pyproject.toml` 的那一层）打开 PowerShell：

```powershell
# 完整安装
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1

# 不注册后台任务
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -NoAgent

# 只看计划，不写任何文件
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -DryRun
```

参数：

| 参数 | 作用 |
|---|---|
| `-NoAgent` | 不注册后台看护任务（`ai-rulec service install`） |
| `-NoUi` | 结束时不打开本地控制台 |
| `-SkipProbe` | 构建时跳过端点探测（更快，服务目录少了实测结果） |
| `-NoMihomo` | 跳过 `mihomo.exe` 检查（仅跳过检查，构建流程不变） |
| `-DryRun` | 打印将执行的步骤，不写文件；Python 版本检查照跑 |
| `-Python <path>` | 用来建 venv 的解释器，默认先找 `py -3`，再找 PATH 上的 `python` |
| `-Project <dir>` | 仓库根目录，默认是本脚本的上一级 |

`-DryRun` 的退出点在 Python 通过检查之后：脚本打印一份计划（`keep` / `run` / `fetch` / `skip` 四类行），然后直接 `exit 0` ——venv、pip、拉源、build、apply、service、ui 一步都不执行。它**不会**替你判断 mihomo 是否存在：那一项在真实运行时才检查，所以 `-DryRun` 的计划里不出现它。

安装器只做前置检查和调用编排，**不自己调 `schtasks`，也不自己写任务定义**。第 7 步是：

```powershell
ai-rulec service install --meta out\mihomo-meta.yaml --python .venv\Scripts\python.exe --interval 5 --agents-interval 300
```

任务名、触发方式、命令行都由这个子命令负责；`ai-rulec service describe --json` 会打印它准备执行的确切 `schtasks` 参数而不写任何东西（不带 `--json` 时只打印任务名、后端和命令行）。

`-NoMihomo` 只跳过「PATH 里有没有 `mihomo.exe`」这项检查；没有 mihomo 时 `build` 的校验会把 `mihomo` 记为 `skipped` 并给一条 warning，产物照常生成。事后想补校验：装上 mihomo 再跑 `ai-rulec verify`。

## mihomo 校验器

`build` / `apply` / `verify` / `watch` 在找到 mihomo 时用 `mihomo -t` 验证配置能否加载（`build` 若带 `--skip-verify` 则例外）。没有它时校验被跳过，只在输出里留一条 warning，看起来像通过，所以安装器默认检查 PATH 里有没有 `mihomo.exe`：

1. 打开 <https://github.com/MetaCubeX/mihomo/releases>，下载 `mihomo-windows-amd64-*.zip`（ARM64 机器选 `arm64`）；
2. 解压出 `mihomo.exe`，例如放进 `%LOCALAPPDATA%\mihomo`（路径自己定，别放需要管理员权限的目录）；
3. 把该目录加进**用户** PATH：`设置 → 系统 → 系统信息 → 高级系统设置 → 环境变量`，或

   ```powershell
   [Environment]::SetEnvironmentVariable(
       'Path',
       [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + "$env:LOCALAPPDATA\mihomo",
       'User'
   )
   ```

4. **开一个新终端**（旧终端不会读到你刚改的 PATH），确认 `mihomo -v`。

## 目录与日志

全部由 `ai_rulec/platforms.py` 决定，没有自定义环境变量时是：

| 用途 | 路径 |
|---|---|
| 配置（`config_home()`） | `%APPDATA%`（缺省 `%USERPROFILE%\AppData\Roaming`） |
| 数据（`data_home()`） | `%LOCALAPPDATA%`（缺省 `%USERPROFILE%\AppData\Local`） |
| 可变状态（最近一次 build、watch 状态） | `%LOCALAPPDATA%\ai-rulec\`（`paths.state_dir()`，可用 `AI_RULEC_STATE` 覆盖） |
| 服务日志目录（`log_dir()`） | `%LOCALAPPDATA%\ai-rulec\logs\`（macOS/Linux 的守护进程写这里；**Windows 上计划任务不重定向输出，不会生成日志文件**，见下） |
| 仓库内产物 | `out\`、`.sources\`、`.venv\`（都不随仓库分发） |
| 客户端配置候选 | `%APPDATA%\FlClash`、`%APPDATA%\com.follow.clash`、`%LOCALAPPDATA%\com.follow.clash`、`%APPDATA%\io.github.clash-verge-rev.clash-verge-rev`、`%APPDATA%\clash-verge`、`%APPDATA%\Stash`、`%APPDATA%\mihomo`、`~\.config\clash`、`~\.config\mihomo` |

候选顺序来自 `platforms.client_roots()`，**这些路径是按各客户端惯例推断的，没有在真机上逐个核实**。以 `ai-rulec discover` 的实际输出为准；找不到时用 `--root` 显式指定配置目录。

看服务是否在跑：

```powershell
ai-rulec service status      # 是否注册/在跑（schtasks 后端不读日志文件）
ai-rulec service describe    # 任务名、命令行、工作目录（不写任何文件）
Get-ScheduledTask -TaskName ai-rulec-watch | Format-List
```

计划任务不重定向 stdout/stderr，所以 `%LOCALAPPDATA%\ai-rulec\logs\` 在 Windows 上**不会有** `ai-rulec-watch.log`。要看守护进程说了什么，只有两条路：`Get-ScheduledTaskInfo` 看退出码，或前台跑一次（见下一节最后一条命令）。

## 计划任务

后台看护是**当前用户**的 Task Scheduler 任务，任务名 `ai-rulec-watch`：

- 触发方式是 `/SC ONLOGON`：**登录后**才启动。`/SC ONSTART`（开机即跑、早于登录）需要管理员 shell，安装器不会自动提权，也不会尝试。
- 不需要管理员权限：任务以当前用户身份运行，不注册系统级计划任务。
- 命令行形如：`<venv 的 python.exe> -m ai_rulec watch --latest --meta <out>\mihomo-meta.yaml --interval 5 --health --agents-interval 300 --json`。工作目录是 `ai_rulec` 包所在的仓库目录，所以仓库不能挪走——挪了要重新 `ai-rulec service install`。
- **Task Scheduler 会丢弃 stdout/stderr**：`--json` 的输出流不会被存成日志文件。想知道实时状态用 `ai-rulec service status`，不要指望在任务历史里看到规则细节。

常用操作：

```powershell
# 立刻跑一次，不用等登录
Start-ScheduledTask -TaskName ai-rulec-watch

# 停掉（只是停这一次，任务仍在，下次登录还会启动）
Stop-ScheduledTask -TaskName ai-rulec-watch

# 看上次运行结果与退出码
Get-ScheduledTaskInfo -TaskName ai-rulec-watch

# 手动排查：直接跑同样的命令，前台看输出
.\.venv\Scripts\python.exe -m ai_rulec watch --latest --interval 5 --health --json
```

不要把任务改成「不管用户是否登录都运行」：那会把任务挪到服务会话里，拿不到当前用户的 `%APPDATA%`，也读不到用户客户端配置。

## 卸载

```powershell
ai-rulec service uninstall            # 注销并停止后台任务，不动 profile 和产物
Remove-Item -Recurse -Force .venv     # 删掉本仓库的虚拟环境
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\ai-rulec"   # 构建记录、watch 状态、日志（可选）
Remove-Item -Recurse -Force .sources, out                  # 拉下来的来源与产物（可选）
```

profile 侧的改动要自己回滚：`ai-rulec rollback --latest` 恢复 `apply` 之前的客户端配置备份；备份文件保留在客户端配置目录里，不会随卸载删除。

若之前手动改过 PATH 里的 mihomo 目录，卸载后可以自己删掉那一项；`ai-rulec` 不会动你的环境变量。

## 排错

**`... 无法加载文件 ... 因为在此系统上禁止运行脚本`**
默认执行策略是 `Restricted`。只对当前进程放行，不改系统设置：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
# 或者每次都带上：powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

`-Scope Process` 只影响这个窗口；退出即恢复。不要用 `-Scope LocalMachine`，那是系统级改动，需要管理员。

**「Windows 已保护你的电脑」/ SmartScreen 拦下脚本**
从浏览器下载的 `.zip` 会带上「来自 Internet」标记，解压后文件仍被标为不受信任。脚本未签名，SmartScreen 不认识它：

```powershell
Unblock-File .\scripts\install.ps1
```

更彻底的做法是在解压**之前**右键 `.zip` → 属性 → 勾选「解除锁定」→ 再解压。判断是否被标记：`Get-Item .\scripts\install.ps1 -Stream Zone.Identifier -ErrorAction SilentlyContinue`。

**`apply` 报 `PermissionError`，或提示 `close the client that holds this file open and retry`**
FlClash 正在运行并占着 profile 文件。Windows 的文件锁是强制的，改名/替换会失败。`platforms.atomic_replace()` 只重试 5 次、每次 0.15 秒——那是为了扛住杀毒软件的瞬时扫描，不是等你关窗口。彻底退出客户端（托盘图标 → 退出，并在任务管理器里确认 `FlClash.exe` 已消失），然后重跑：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -NoAgent
```

改完 profile 不生效是另一回事：FlClash / mihomo 需要重启或重新加载 profile 才会读到新配置；`watch` 只负责让规则和 provider cache 不漂移。

**找不到 Python / `python` 打开的是微软商店**
商店版 `python.exe` 是个跳转壳，`py -3` 能找到真解释器时安装器优先用它。都没有就装官方版，安装时勾选 **Add python.exe to PATH**，然后**开新终端**。也可以直接 `-Python "C:\Python312\python.exe"`。

**`git` 未找到，规则源拉不下来**
按提示二选一：装 Git for Windows 后开新终端重跑；或在 Git Bash 里跑一次 `./scripts/fetch_sources.sh` 再回来。已有 `.sources` 时安装器不要求 git。

**下载规则源失败（`download failed: ... 403/超时`）**
企业网络或 GitHub 被拦。设置代理后重跑，或在 Git Bash 里跑 `fetch_sources.sh`（它用 `curl`，会读 `HTTPS_PROXY`）：

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

安装器显式把 TLS 设成 1.2（部分 Win10 1809 默认更低），不需要你改注册表。

**路径太长 / 含中文导致奇怪失败**
部分客户端和工具仍受 `MAX_PATH`（260）限制，即使系统开了长路径支持。把仓库放到短 ASCII 路径，例如 `C:\src\ai_proxy`，再重跑。中文路径本身是支持的：安装器设了 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`，但老版本客户端可能不认。

**规则文件变成了复制而不是符号链接**
Windows 默认权限下普通用户不能建符号链接，`platforms.link_or_copy()` 会退回复制。路由语义相同，但源规则变化后不会自动同步——重跑 `apply` 或让 `watch` 处理即可。想用符号链接，得开「开发者模式」或用管理员终端。

**计划任务注册成功但没在跑**
`/SC ONLOGON` 只在下一次登录时触发；现在想验证就 `Start-ScheduledTask -TaskName ai-rulec-watch`，再用 `Get-ScheduledTaskInfo` 看 `LastTaskResult`。仓库被移动或 venv 被删也会让它起不来，重新 `ai-rulec service install` 即可。

**`ai-rulec` 命令在别的终端里找不到**
venv 里的控制台脚本在 `.venv\Scripts\`，没有进全局 PATH。用完整路径调用，或先激活：

```powershell
.\.venv\Scripts\Activate.ps1
ai-rulec status
```

## 已知缺口

- **未在真机验证**：本文和 `scripts/install.ps1` 都是在 macOS 上写的，没有在 Windows 上整体跑过。脚本语法只做了括号/引号配平和 PS 7-only 语法的静态排查（本机没有 `pwsh`，无法用 `[Parser]::ParseFile`）：`??` / `?.` / `&&` / `||` / 三元表达式、`-AsHashtable`、`::new()` 一律没用，取而代之的是 `if` 与 `@()` 数组。
- **客户端路径是推断的**：`platforms.client_roots()` 的 Windows 条目没有逐个对照真实安装验证，以 `ai-rulec discover` 为准。
- **SmartScreen 未签名**：安装器不签名，也不下载签名工具；首次运行需要自己解除锁定。
- **任务不使用 `ONSTART`**：开机未登录时不跑，这是刻意的（无管理员权限，且拿不到用户配置）。
