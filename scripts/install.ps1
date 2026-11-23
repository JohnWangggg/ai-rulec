#requires -Version 5.1
<#
.SYNOPSIS
    Windows installer for ai-rulec (Windows 10 1809+ / Windows 11).

.DESCRIPTION
    venv -> dependencies -> rule sources -> build -> apply to the running
    client -> background watch -> local console. It mirrors install.command
    (macOS) and scripts/install.sh (Linux).

    The background watcher is registered by `ai-rulec service install`; this
    script never calls schtasks, and never writes a Task Scheduler definition
    itself.

    Any failing step stops here with an actionable message; nothing is
    silently skipped.

.PARAMETER NoAgent
    Do not register the background watch task.

.PARAMETER NoUi
    Do not open the local web console at the end.

.PARAMETER SkipProbe
    Skip endpoint probing during build (faster; catalog is less accurate).

.PARAMETER NoMihomo
    Do not look for mihomo.exe (runtime config validation is skipped anyway).

.PARAMETER DryRun
    Print the steps that would run and change nothing on disk.
    The Python check still runs so a broken interpreter is caught early.

.PARAMETER Python
    Absolute path to a Python >= 3.10 interpreter.
    Default: the `py -3` launcher, then `python` on PATH.

.PARAMETER Project
    Repository root. Default: the directory above this script.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -NoAgent -SkipProbe

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -DryRun

.NOTES
    Keep the source list in Get-Sources in sync with scripts/fetch_sources.sh.
#>
[CmdletBinding()]
param(
    [switch]$NoAgent,
    [switch]$NoUi,
    [switch]$SkipProbe,
    [switch]$NoMihomo,
    [switch]$DryRun,
    [string]$Python = '',
    [string]$Project = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Python writes UTF-8 regardless of the console code page (CJK paths, CJK
# service names). Both variables are read by CPython, not by Windows.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Write-Step {
    param([string]$Text)
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Text)
    Write-Host "WARNING: $Text" -ForegroundColor Yellow
}

function Fail {
    param([string]$Message, [string[]]$Hints = @())
    Write-Host ''
    Write-Host "ERROR: $Message" -ForegroundColor Red
    foreach ($hint in $Hints) {
        Write-Host "  $hint" -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host 'Troubleshooting: docs\WINDOWS.md'
    exit 1
}

function Write-Troubleshooting {
    Write-Host ''
    Write-Host 'Troubleshooting:' -ForegroundColor Yellow
    Write-Host '  * script blocked by execution policy (docs\WINDOWS.md):'
    Write-Host '      powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1'
    Write-Host '  * "Windows protected your PC" / SmartScreen on a downloaded copy:'
    Write-Host '      Unblock-File .\scripts\install.ps1'
    Write-Host '      (or File > Properties > Unblock on the .zip, then extract again)'
    Write-Host '  * apply failed with PermissionError: quit FlClash completely'
    Write-Host '      (tray icon > Quit, then wait until FlClash.exe is gone) and re-run.'
    Write-Host '  * long or CJK paths: move the repo to a short ASCII path, e.g. C:\src\ai_proxy'
    Write-Host '      (Windows MAX_PATH applies to some clients even with long-path support on).'
}

# Run an external program, fail on a non-zero exit code.
function Invoke-Native {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$What
    )
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

# ---------------------------------------------------------------------------
# 0. Locate the project
# ---------------------------------------------------------------------------
$ProjectDir = $Project
if (-not $ProjectDir) {
    $ProjectDir = Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $ProjectDir -PathType Container)) {
    Fail "project directory not found: $ProjectDir" @('pass -Project <dir> pointing at the ai-rulec checkout')
}
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path

$VenvDir = Join-Path $ProjectDir '.venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
$AiRulecExe = Join-Path $VenvDir 'Scripts\ai-rulec.exe'
$SourcesDir = Join-Path $ProjectDir '.sources'
$OutDir = Join-Path $ProjectDir 'out'
$MetaPath = Join-Path $OutDir 'mihomo-meta.yaml'

Push-Location -LiteralPath $ProjectDir

Write-Host 'ai-rulec installer (Windows)' -ForegroundColor Green
Write-Host "  project: $ProjectDir"

try {
    # -----------------------------------------------------------------------
    # 1. Python >= 3.10
    # -----------------------------------------------------------------------
    Write-Step '1/8 Checking Python'

    $PyExe = ''
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python)) {
            Fail "Python not found at: $Python" @(
                'install Python 3.10+ from https://www.python.org/downloads/windows/',
                'or pass -Python "C:\Path\To\python.exe"'
            )
        }
        $PyExe = (Resolve-Path -LiteralPath $Python).Path
    }
    else {
        $candidates = @()
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $candidates += [pscustomobject]@{ Exe = 'py'; Args = @('-3') }
        }
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $candidates += [pscustomobject]@{ Exe = 'python'; Args = @() }
        }
        foreach ($candidate in $candidates) {
            $probe = @($candidate.Args) + @('-c', 'import sys; print(sys.executable)')
            $resolved = ''
            try {
                $resolved = & $candidate.Exe @probe
            }
            catch {
                $resolved = ''
            }
            if ($LASTEXITCODE -eq 0 -and $resolved) {
                $resolved = [string](@($resolved)[-1])
                if (Test-Path -LiteralPath $resolved) {
                    $PyExe = $resolved
                    break
                }
            }
        }
    }

    if (-not $PyExe) {
        Fail 'no Python 3 interpreter found ("py -3" and "python" both failed)' @(
            'install Python 3.10+ from https://www.python.org/downloads/windows/',
            'tick "Add python.exe to PATH" in the installer, then open a NEW terminal',
            'or pass -Python "C:\Path\To\python.exe"',
            'note: the Microsoft Store stub named python.exe does not count'
        )
    }

    $pyVersion = & $PyExe -c 'import sys; print("%d.%d.%d" % sys.version_info[:3]); raise SystemExit(0 if sys.version_info >= (3, 10) else 3)'
    $pyVersion = [string](@($pyVersion)[-1])
    if ($LASTEXITCODE -eq 3) {
        Fail "Python $pyVersion at $PyExe is too old; ai-rulec needs >= 3.10" @(
            'install Python 3.10+ and pass -Python "C:\Path\To\python.exe"'
        )
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "could not run $PyExe (exit code $LASTEXITCODE)" @(
            'pass -Python with a working interpreter'
        )
    }
    Write-Host "  Python $pyVersion ($PyExe)"

    if ($DryRun) {
        $probeArgs = ''
        if (-not $SkipProbe) {
            $probeArgs = ' --probe --probe-proxy auto'
        }
        Write-Host ''
        Write-Host 'Plan (nothing is executed with -DryRun):' -ForegroundColor Cyan
        if (Test-Path -LiteralPath $VenvPy) {
            Write-Host "  keep   $VenvDir"
        }
        else {
            Write-Host "  run    `"$PyExe`" -m venv `"$VenvDir`""
        }
        Write-Host "  run    `"$VenvPy`" -m pip install --upgrade pip"
        Write-Host "  run    `"$VenvPy`" -m pip install --upgrade setuptools wheel"
        Write-Host "  run    `"$VenvPy`" -m pip install --no-build-isolation -e `"$ProjectDir`""
        Write-Host "  run    ai-rulec build --formats all --asn-mode drop --out `"$OutDir`"$probeArgs"
        Write-Host "  run    ai-rulec apply --latest --meta `"$MetaPath`""
        if ($NoAgent) {
            Write-Host '  skip   ai-rulec service install (-NoAgent)'
        }
        else {
            Write-Host "  run    ai-rulec service install --meta `"$MetaPath`" --python `"$VenvPy`" --interval 5 --agents-interval 300"
        }
        if ($NoUi) {
            Write-Host '  skip   ai-rulec ui (-NoUi)'
        }
        else {
            Write-Host '  run    ai-rulec ui'
        }
        if (Test-Path -LiteralPath $SourcesDir) {
            Write-Host "  fetch  $SourcesDir (only the missing repositories and files)"
        }
        else {
            Write-Host "  fetch  $SourcesDir (5 git clones and 9 downloads; needs git)"
        }
        Write-Host ''
        Write-Host 'Dry run complete. Nothing was changed.' -ForegroundColor Green
        exit 0
    }

    # -----------------------------------------------------------------------
    # 2. Virtual environment
    # -----------------------------------------------------------------------
    Write-Step '2/8 Preparing the virtual environment'

    if (-not (Test-Path -LiteralPath $VenvPy)) {
        Invoke-Native -Exe $PyExe -Arguments @('-m', 'venv', $VenvDir) -What 'python -m venv'
    }
    if (-not (Test-Path -LiteralPath $VenvPy)) {
        Fail "virtual environment is missing: $VenvPy" @('delete the .venv folder and re-run this script')
    }

    $venvVersion = & $VenvPy -c 'import sys; print("%d.%d.%d" % sys.version_info[:3]); raise SystemExit(0 if sys.version_info >= (3, 10) else 3)'
    $venvVersion = [string](@($venvVersion)[-1])
    if ($LASTEXITCODE -eq 3) {
        Fail "the existing .venv uses Python $venvVersion, but ai-rulec needs >= 3.10" @(
            "delete $VenvDir and re-run this script"
        )
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "the existing .venv cannot run (exit code $LASTEXITCODE): $VenvPy" @(
            "delete $VenvDir and re-run this script"
        )
    }
    Write-Host "  venv Python $venvVersion"

    function Invoke-Pip {
        param([string[]]$Arguments, [string]$What)
        & $VenvPy -m pip install @Arguments
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn "pip failed with the default index; retrying with https://pypi.org/simple"
        & $VenvPy -m pip install @Arguments -i https://pypi.org/simple
        if ($LASTEXITCODE -ne 0) {
            Fail "pip install failed: $What" @(
                'check network/proxy settings (HTTPS_PROXY), then re-run this script',
                'pinned fallback index: https://pypi.org/simple'
            )
        }
    }

    Invoke-Pip -Arguments @('--upgrade', 'pip') -What 'pip upgrade'
    Invoke-Pip -Arguments @('--upgrade', 'setuptools', 'wheel') -What 'setuptools/wheel'
    Invoke-Pip -Arguments @('--no-build-isolation', '-e', $ProjectDir) -What 'editable install of ai-rulec'

    if (-not (Test-Path -LiteralPath $AiRulecExe)) {
        Fail "console script not created: $AiRulecExe" @(
            "run: `"$VenvPy`" -m pip install --no-build-isolation -e `"$ProjectDir`""
        )
    }
    & $AiRulecExe --version
    if ($LASTEXITCODE -ne 0) {
        Fail "ai-rulec --version failed (exit code $LASTEXITCODE)" @('delete the .venv folder and re-run this script')
    }

    # The dist-info can be present while the import fails (broken mirror,
    # half-removed PyYAML), and every command needs it.
    $yamlVersion = & $VenvPy -c 'import yaml; print(yaml.__version__)'
    $yamlVersion = [string](@($yamlVersion)[-1])
    if ($LASTEXITCODE -ne 0) {
        Fail 'PyYAML is missing from the virtual environment' @(
            "run: `"$VenvPy`" -m pip install `"PyYAML>=6.0`""
        )
    }
    Write-Host "  PyYAML $yamlVersion"

    # -----------------------------------------------------------------------
    # 3. mihomo (optional runtime validator)
    # -----------------------------------------------------------------------
    Write-Step '3/8 Checking the mihomo validator'

    if ($NoMihomo) {
        Write-Host '  skipped (-NoMihomo); runtime config validation will be skipped'
    }
    else {
        $mihomoCmd = Get-Command mihomo -ErrorAction SilentlyContinue
        if ($mihomoCmd) {
            Write-Host "  mihomo: $($mihomoCmd.Source)"
        }
        else {
            Write-Warn 'mihomo.exe not found; runtime config validation (mihomo -t) will be skipped'
            Write-Host '  install it, then add the folder to PATH:'
            Write-Host '    1. https://github.com/MetaCubeX/mihomo/releases  (mihomo-windows-amd64-*.zip)'
            Write-Host '    2. extract mihomo.exe, e.g. into %LOCALAPPDATA%\mihomo'
            Write-Host '    3. add that folder to the user PATH, open a NEW terminal, re-run'
            Write-Host '       (docs\WINDOWS.md has the step-by-step; -NoMihomo silences this)'
        }
    }

    # -----------------------------------------------------------------------
    # 4. Rule sources
    # -----------------------------------------------------------------------
    Write-Step '4/8 Preparing rule sources'

    function Get-Sources {
        param([string]$Root)

        # Mirrors scripts/fetch_sources.sh. If the two ever drift, running that
        # bash script from Git Bash is authoritative; this copy only exists so a
        # plain PowerShell session can bootstrap without Git Bash semantics.
        $repos = @(
            @{ Url = 'https://github.com/v2fly/domain-list-community.git'; Dir = 'v2fly_domain-list-community' },
            @{ Url = 'https://github.com/jinxinkai/clash-ai-rules.git'; Dir = 'jinxinkai_clash-ai-rules' },
            @{ Url = 'https://github.com/G4free/clash-ruleset.git'; Dir = 'G4free_clash-ruleset' },
            @{ Url = 'https://github.com/ma-wenqian/ai-proxy-rules.git'; Dir = 'ma-wenqian_ai-proxy-rules' },
            @{ Url = 'https://github.com/SukkaW/Surge.git'; Dir = 'SukkaW_Surge' }
        )
        $bm7Names = @('OpenAI', 'Anthropic', 'Claude', 'Gemini', 'BardAI', 'Copilot')
        $liteNames = @('direct', 'proxy', 'reject')

        $git = Get-Command git -ErrorAction SilentlyContinue
        $missing = @($repos | Where-Object {
                -not (Test-Path -LiteralPath (Join-Path $Root $_.Dir) -PathType Container)
            })
        if ($missing.Count -gt 0 -and -not $git) {
            $names = ($missing | ForEach-Object { $_.Dir }) -join ', '
            Fail "git not found, so the rule sources cannot be fetched: $names" @(
                'option A: install Git for Windows (https://git-scm.com/download/win),',
                '          open a NEW terminal, re-run this script',
                'option B: in Git Bash run  ./scripts/fetch_sources.sh  ,',
                '          then re-run this script (an existing .sources is never re-fetched)'
            )
        }

        foreach ($repo in $repos) {
            $target = Join-Path $Root $repo.Dir
            if (Test-Path -LiteralPath $target -PathType Container) {
                Write-Host "  keep  $($repo.Dir)"
                continue
            }
            Write-Host "  clone $($repo.Dir)"
            Invoke-Native -Exe $git.Source -Arguments @('clone', '--depth', '1', $repo.Url, $target) -What "git clone $($repo.Url)"
        }

        # Per-file, not per-directory: an interrupted run must not leave a
        # half-filled folder that a re-run then treats as complete.
        $bm7Dir = Join-Path $Root 'bm7'
        New-Item -ItemType Directory -Path $bm7Dir -Force | Out-Null
        foreach ($name in $bm7Names) {
            $out = Join-Path $bm7Dir "$name.yaml"
            if (Test-Path -LiteralPath $out) {
                Write-Host "  keep  bm7/$name.yaml"
                continue
            }
            $uri = "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash/$name/$name.yaml"
            Write-Host "  fetch bm7/$name.yaml"
            try {
                Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $out
            }
            catch {
                throw "download failed: $uri ($($_.Exception.Message)); set HTTPS_PROXY if GitHub is blocked"
            }
        }

        $liteDir = Join-Path $Root 'loyalsoldier'
        New-Item -ItemType Directory -Path $liteDir -Force | Out-Null
        foreach ($name in $liteNames) {
            $out = Join-Path $liteDir "$name.txt"
            if (Test-Path -LiteralPath $out) {
                Write-Host "  keep  loyalsoldier/$name.txt"
                continue
            }
            $uri = "https://github.com/Loyalsoldier/clash-rules/releases/latest/download/$name.txt"
            Write-Host "  fetch loyalsoldier/$name.txt"
            try {
                Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $out
            }
            catch {
                throw "download failed: $uri ($($_.Exception.Message)); set HTTPS_PROXY if GitHub is blocked"
            }
        }
    }

    # Incremental by construction: every clone and every download is skipped
    # when its target already exists, so a re-run repairs a partial fetch.
    if (Test-Path -LiteralPath $SourcesDir) {
        Write-Host "  reusing $SourcesDir (missing pieces are fetched below)"
    }
    # TLS 1.2 explicitly: some Windows 10 1809 profiles still default lower.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Get-Sources -Root $SourcesDir

    # build reads SOURCE_ROOT first; an absolute path keeps it independent of
    # the directory the caller happened to use.
    $env:SOURCE_ROOT = $SourcesDir

    # -----------------------------------------------------------------------
    # 5. Build
    # -----------------------------------------------------------------------
    Write-Step '5/8 Compiling rule sets'

    $buildArgs = @('build', '--formats', 'all', '--asn-mode', 'drop', '--out', $OutDir)
    if (-not $SkipProbe) {
        $buildArgs += @('--probe', '--probe-proxy', 'auto')
    }
    else {
        Write-Host '  probing skipped (-SkipProbe)'
    }

    & $AiRulecExe @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "build failed (exit code $LASTEXITCODE)" @(
            "check $SourcesDir (delete it and re-run to re-fetch)",
            'a missing proxy is not a build error: probe failures are reported per domain'
        )
    }
    if (-not (Test-Path -LiteralPath $MetaPath)) {
        Fail "build finished but $MetaPath is missing" @("delete $OutDir and re-run this script")
    }
    Write-Host "  meta: $MetaPath"

    # -----------------------------------------------------------------------
    # 6. Apply to the running client
    # -----------------------------------------------------------------------
    Write-Step '6/8 Applying to the current proxy client'

    $client = Get-Process -Name 'FlClash' -ErrorAction SilentlyContinue
    if ($client) {
        Write-Warn 'FlClash is running and may hold its config file open; quit it (tray icon > Quit) if apply fails'
    }

    & $AiRulecExe apply --latest --meta $MetaPath
    if ($LASTEXITCODE -ne 0) {
        Fail "apply failed (exit code $LASTEXITCODE)" @(
            'if the error says PermissionError / "close the client that holds this file open":',
            '  quit FlClash completely (tray icon > Quit; confirm FlClash.exe is gone in Task Manager)',
            '  then re-run:  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1',
            'the retries in ai_rulec.platforms.atomic_replace are short (5 x 0.15s); they are not a wait loop'
        )
    }

    # -----------------------------------------------------------------------
    # 7. Background watch task
    # -----------------------------------------------------------------------
    if ($NoAgent) {
        Write-Step '7/8 Skipping the background watcher (-NoAgent)'
    }
    else {
        Write-Step '7/8 Registering the background watcher'
        & $AiRulecExe service install --meta $MetaPath --python $VenvPy --interval 5 --agents-interval 300
        if ($LASTEXITCODE -ne 0) {
            Fail "service install failed (exit code $LASTEXITCODE)" @(
                'inspect: Get-ScheduledTask -TaskName ai-rulec-watch',
                'the task is registered for the current user only; no admin rights are needed'
            )
        }
        Write-Host '  inspect with: Get-ScheduledTask -TaskName ai-rulec-watch | Format-List'
        Write-Host '  live state:   ai-rulec service status   (the task does not write log files)'
    }
}
catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Troubleshooting
    exit 1
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host "  console script: $AiRulecExe"
Write-Host "  meta:           $MetaPath"

# ---------------------------------------------------------------------------
# 8. Local console
# ---------------------------------------------------------------------------
# Outside the try block on purpose: Ctrl+C is how this step normally ends,
# and it must not print the ERROR/troubleshooting banner.
if ($NoUi) {
    Write-Step '8/8 Skipping the local console (-NoUi)'
}
else {
    Write-Step '8/8 Opening the local console (Ctrl+C to stop)'
    & $AiRulecExe ui
}
