# resolve-editor setup for Windows (Windows PowerShell 5.1 or PowerShell 7).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1              create .venv (if missing) and install packages
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Recreate    delete and rebuild .venv
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Dev         also install the test packages
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mcp         also register Resolve's AI assistant server with
#                                                                     Claude Code (only if not registered yet and the
#                                                                     ResolveMCP program exists)
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Asr small   set up local transcription: the pywhispercpp
#                                                                     package and a whisper model (asks before
#                                                                     downloading; base 142 MB, small 466 MB)
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Asr small -Yes   the same without asking
#   powershell -ExecutionPolicy Bypass -File install.ps1 -CheckLocation  only check that this folder sits where
#                                                                     Claude Code looks for skills
#
# It only writes inside this folder (.venv and models). No admin rights, no system changes.

param(
    [switch]$Recreate,
    [switch]$Dev,
    [switch]$Mcp,
    [string]$Asr = "",
    [switch]$Yes,
    [switch]$CheckLocation
)

$ErrorActionPreference = "Continue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Problems = $false
$AsrSkipped = $false

function Say([string]$msg) { Write-Host $msg }
function Warn([string]$msg) { Write-Host ("WARNING: " + $msg) -ForegroundColor Yellow }

if ($Asr -and ($Asr -ne "base") -and ($Asr -ne "small")) {
    Say ("Unknown whisper model for -Asr: " + $Asr + " (use base or small)")
    exit 2
}

Say ("resolve-editor setup in: " + $Here)

# 1. Where the folder lives: Claude Code only finds a skill whose folder sits directly in a .claude\skills folder
#    (%USERPROFILE%\.claude\skills\resolve-editor). Windows "Extract All" adds a second folder level
#    (resolve-editor\resolve-editor), which Claude Code never finds.
$Name = Split-Path -Leaf $Here
$Parent = Split-Path -Parent $Here
$ParentName = Split-Path -Leaf $Parent
$GrandPath = Split-Path -Parent $Parent
$GrandName = Split-Path -Leaf $GrandPath
# A custom Claude Code config folder (CLAUDE_CONFIG_DIR) holds its own skills folder too.
$ConfigDirOk = $false
if ($env:CLAUDE_CONFIG_DIR) {
    try {
        $Cfg = (Resolve-Path -LiteralPath $env:CLAUDE_CONFIG_DIR -ErrorAction Stop).Path.TrimEnd('\', '/')
        $Grand = (Resolve-Path -LiteralPath $GrandPath -ErrorAction Stop).Path.TrimEnd('\', '/')
        $ConfigDirOk = ($Cfg -ieq $Grand)
    } catch { $ConfigDirOk = $false }
}
if (-not (($ParentName -ieq "skills") -and (($GrandName -ieq ".claude") -or $ConfigDirOk))) {
    Warn "this folder is in the wrong place, so Claude Code will not find the skill:"
    Say ("         " + $Here)
    if ($ParentName -ieq $Name) {
        Say "         It is a folder inside a folder of the same name (Extract All added a level)."
        Say ("         Move the inner " + $Name + " folder up one level so it replaces the outer one:")
        Say ("           $env:USERPROFILE\.claude\skills\" + $Name + "\SKILL.md must exist, not ...\" + $Name + "\" + $Name + "\SKILL.md")
    } else {
        Say ("         Move the whole folder so that this file exists: $env:USERPROFILE\.claude\skills\" + $Name + "\SKILL.md")
    }
    Say "         Then run this script again from the new place. Nothing was installed."
    exit 2
}
if ($CheckLocation) {
    Say "Location OK: Claude Code will find the skill here."
    exit 0
}

# 2. Python 3.10 or newer (the py launcher first, then python on PATH)
$PyExe = $null
$PyArgs = @()
$candidates = @(
    @{ exe = "py"; args = @("-3") },
    @{ exe = "python"; args = @() },
    @{ exe = "python3"; args = @() }
)
foreach ($c in $candidates) {
    $cmd = Get-Command $c.exe -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { continue }
    # The Microsoft Store placeholder python.exe prints nothing useful; the version check filters it out.
    $check = "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    & $c.exe @($c.args + @("-c", $check)) 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $PyExe = $c.exe
        $PyArgs = $c.args
        break
    }
}
if ($null -eq $PyExe) {
    Say "Python 3.10 or newer was not found."
    Say "Install it with: winget install Python.Python.3.12   (or from https://www.python.org/downloads/)"
    Say "Then open a new PowerShell window and run this script again."
    exit 1
}
$ver = & $PyExe @($PyArgs + @("-c", "import sys; print(sys.version.split()[0])"))
Say ("Python: " + $PyExe + " " + ($PyArgs -join " ") + " (" + $ver + ")")

# 3. The virtual environment
$Venv = Join-Path $Here ".venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
if ($Recreate -and (Test-Path $Venv)) {
    Say "Removing the old .venv"
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $VPy)) {
    Say "Creating .venv"
    & $PyExe @($PyArgs + @("-m", "venv", $Venv))
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VPy)) {
        Say "Could not create the virtual environment."
        exit 1
    }
} else {
    Say ".venv already exists (use -Recreate to rebuild it)"
}

$Req = Join-Path $Here "requirements.txt"
if ($Dev) { $Req = Join-Path $Here "requirements-dev.txt" }
Say ("Installing packages from " + (Split-Path -Leaf $Req))
& $VPy -m pip install --disable-pip-version-check -q -r $Req
if ($LASTEXITCODE -ne 0) {
    Warn "pip could not install the packages (no internet connection?). Run this script again when you are online."
    $Problems = $true
}
& $VPy -c "import numpy, PIL" 2>$null
if ($LASTEXITCODE -eq 0) {
    $pk = & $VPy -c "import numpy, PIL; print('numpy', numpy.__version__, '/ pillow', PIL.__version__)"
    Say ("Packages: " + $pk)
} else {
    Warn "numpy and pillow are not importable from .venv yet."
    $Problems = $true
}

# 4. ffmpeg and ffprobe
foreach ($tool in @("ffmpeg", "ffprobe")) {
    $t = Get-Command $tool -ErrorAction SilentlyContinue
    if ($null -ne $t) {
        Say ($tool + ": " + $t.Path)
    } else {
        Warn ($tool + " was not found on PATH.")
        Say "         Install it with: winget install Gyan.FFmpeg   (then open a new window and restart Claude Code)"
        Say "         Or point the skill at your own copy with the RE_FFMPEG and RE_FFPROBE environment variables."
        $Problems = $true
    }
}

# 5. Local transcription (optional): whisper-cli (whisper.cpp) or the pywhispercpp package, plus a model file
$Whisper = $null
if ($env:RE_WHISPER_CLI -and (Test-Path $env:RE_WHISPER_CLI)) {
    $Whisper = $env:RE_WHISPER_CLI
} else {
    $wc = Get-Command whisper-cli -ErrorAction SilentlyContinue
    if ($null -ne $wc) { $Whisper = $wc.Path }
}
function Test-Pyw {
    & $VPy -c "import pywhispercpp" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function Find-Model {
    if ($env:RE_WHISPER_MODEL -and (Test-Path $env:RE_WHISPER_MODEL)) { return $env:RE_WHISPER_MODEL }
    $dirs = @((Join-Path $Here "models"), (Join-Path $env:USERPROFILE ".cache\whisper-models"),
              (Join-Path $env:USERPROFILE ".cache\whisper.cpp"))
    foreach ($d in $dirs) {
        if (Test-Path $d) {
            $f = Get-ChildItem -Path $d -Filter "ggml-*.bin" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -ne $f) { return $f.FullName }
        }
    }
    return $null
}

if ($Asr) {
    if ((-not $Whisper) -and (-not (Test-Pyw))) {
        Say "Installing the whisper.cpp Python binding (requirements-asr.txt)"
        & $VPy -m pip install --disable-pip-version-check -q -r (Join-Path $Here "requirements-asr.txt")
        if ($LASTEXITCODE -ne 0) {
            Warn "pywhispercpp could not be installed. Download whisper.cpp's whisper-bin-x64.zip from its GitHub releases, and set RE_WHISPER_CLI to its whisper-cli.exe, or run without transcripts."
            $Problems = $true
        }
    }
    $ModelDir = Join-Path $Here "models"
    $ModelFile = Join-Path $ModelDir ("ggml-" + $Asr + ".bin")
    $Size = if ($Asr -eq "base") { "142 MB" } else { "466 MB" }
    $Url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-" + $Asr + ".bin"
    $Existing = $null
    foreach ($f in @($env:RE_WHISPER_MODEL, $ModelFile,
                     (Join-Path $env:USERPROFILE (".cache\whisper-models\ggml-" + $Asr + ".bin")),
                     (Join-Path $env:USERPROFILE (".cache\whisper.cpp\ggml-" + $Asr + ".bin")))) {
        if ($f -and (Test-Path $f) -and ((Split-Path -Leaf $f) -ieq ("ggml-" + $Asr + ".bin"))) { $Existing = $f; break }
    }
    if ($Existing) {
        Say ("Whisper model already present: " + $Existing)
    } else {
        $Go = $false
        if ($Yes) {
            $Go = $true
        } elseif ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
            $ans = Read-Host ("Download the whisper model ggml-" + $Asr + ".bin (" + $Size + ") from the whisper.cpp model repository into models\? [y/N]")
            $Go = ($ans -match '^(y|yes)$')
        } else {
            Warn ("the whisper model ggml-" + $Asr + ".bin (" + $Size + ") was not downloaded: there is no terminal to ask in.")
            Say ("         Ask the user first, then run again with: install.ps1 -Asr " + $Asr + " -Yes")
            $Problems = $true
            $AsrSkipped = $true
        }
        if ($Go) {
            New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null
            Say ("Downloading " + $Url + " (" + $Size + ")")
            $Part = $ModelFile + ".part"
            try {
                $ProgressPreference = "SilentlyContinue"
                Invoke-WebRequest -Uri $Url -OutFile $Part -UseBasicParsing -ErrorAction Stop
                if ((Get-Item $Part).Length -gt 50000000) {
                    Move-Item -Force $Part $ModelFile
                    Say ("Model saved: models\ggml-" + $Asr + ".bin")
                } else {
                    Remove-Item -Force $Part
                    Warn "the downloaded model is too small; run this again when you are online."
                    $Problems = $true
                }
            } catch {
                if (Test-Path $Part) { Remove-Item -Force $Part }
                Warn "the model download failed. Run this again when you are online."
                $Problems = $true
            }
        }
    }
}

$ModelFound = Find-Model
$HasPyw = Test-Pyw
if ($Whisper) {
    Say ("Transcription engine: whisper-cli (" + $Whisper + ")")
} elseif ($HasPyw) {
    Say "Transcription engine: pywhispercpp"
} else {
    Say "Note: no local transcription engine (optional, needed for word-based editing and captions)."
    Say "      Install it with: powershell -ExecutionPolicy Bypass -File install.ps1 -Asr small"
}
if ($Whisper -or $HasPyw) {
    if ($ModelFound) { Say ("Whisper model: " + $ModelFound) }
    elseif (-not $AsrSkipped) { Say "Note: no whisper model file yet. Get one with: install.ps1 -Asr small" }
}

# 6. Resolve's AI assistant server (optional registration)
if ($env:RESOLVE_MCP_PATH) {
    $McpBin = $env:RESOLVE_MCP_PATH
} else {
    $McpBin = Join-Path $env:ProgramFiles "Blackmagic Design\DaVinci Resolve\ResolveMCP.exe"
}
if ($Mcp) {
    if (-not (Test-Path $McpBin)) {
        Warn ("ResolveMCP was not found at: " + $McpBin)
        Say "         It ships with DaVinci Resolve Studio 21.1 or newer. Set RESOLVE_MCP_PATH if it lives elsewhere."
        $Problems = $true
    } elseif ($null -eq (Get-Command claude -ErrorAction SilentlyContinue)) {
        Warn "the claude command was not found, so the server could not be registered."
        $Problems = $true
    } else {
        & claude mcp get davinci-resolve 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { & claude mcp get "DaVinci Resolve" 2>$null | Out-Null }
        $known = ($LASTEXITCODE -eq 0)
        if (-not $known) {
            & claude mcp get "DaVinci Resolve Studio" 2>$null | Out-Null
            $known = ($LASTEXITCODE -eq 0)
        }
        if ($known) {
            Say "Claude Code already knows a DaVinci Resolve server (""DaVinci Resolve"" or ""DaVinci Resolve Studio"")."
        } else {
            Say "Registering the DaVinci Resolve server with Claude Code (user scope)"
            # '--' is quoted so Windows PowerShell passes it on instead of treating it as its own token
            & claude mcp add --scope user davinci-resolve '--' $McpBin
            if ($LASTEXITCODE -ne 0) { Warn "claude mcp add failed"; $Problems = $true }
        }
    }
}

# 7. Self check: both labs report what is missing (doctor exits 0 and reports problems on its first line)
foreach ($lab in @("media_lab.py", "edit_lab.py")) {
    $LabPath = Join-Path $Here $lab
    if (-not (Test-Path $LabPath)) { continue }
    $cmds = @(& $VPy $LabPath commands 2>$null | ForEach-Object { ("$_" -split '\s+')[0] })
    if ($LASTEXITCODE -eq 0 -and ($cmds -contains "doctor")) {
        Say ""
        Say ("Running the skill's own check (" + $lab + " doctor):")
        $doc = @(& $VPy $LabPath doctor 2>&1 | ForEach-Object { "$_" })
        if ($LASTEXITCODE -ne 0) { $Problems = $true }
        $doc | ForEach-Object { Say $_ }
        if ($doc.Count -eq 0 -or $doc[0] -notlike "DOCTOR: OK*") { $Problems = $true }
    }
}

Say ""
Say "Next steps"
Say "1. DaVinci Resolve Studio 21.1 or newer: File > Setup AI Assistants, pick Claude Code. Then restart Claude Code."
Say "   (Or run this script again with -Mcp)"
Say "2. If Resolve tools time out: Resolve > Preferences > System > General > External scripting using: Local."
Say "3. Open your project in Resolve, start Claude Code and ask, for example:"
Say '   "Cut a 45 second Instagram reel from the interview on my current timeline, with captions."'

if ($Problems) {
    Say ""
    Say "Setup finished with warnings (see above)."
    exit 1
}
Say ""
Say "Setup finished."
exit 0
