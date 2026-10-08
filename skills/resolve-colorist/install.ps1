# resolve-colorist setup for Windows (Windows PowerShell 5.1 or PowerShell 7).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1              create .venv (if missing) and install packages
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Recreate    delete and rebuild .venv
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Dev         also install the test packages (colour-science)
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mcp         also register Resolve's AI assistant server with
#                                                                     Claude Code (only if not registered yet and the
#                                                                     ResolveMCP program exists)
#   powershell -ExecutionPolicy Bypass -File install.ps1 -CheckLocation  only check that this folder sits where
#                                                                     Claude Code looks for skills
#
# It only writes inside this folder (.venv). No admin rights, no system changes.

param(
    [switch]$Recreate,
    [switch]$Dev,
    [switch]$Mcp,
    [switch]$CheckLocation
)

$ErrorActionPreference = "Continue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Problems = $false

function Say([string]$msg) { Write-Host $msg }
function Warn([string]$msg) { Write-Host ("WARNING: " + $msg) -ForegroundColor Yellow }

Say ("resolve-colorist setup in: " + $Here)

# 1. Where the folder lives: Claude Code only finds a skill whose folder sits directly in a .claude\skills folder
#    (%USERPROFILE%\.claude\skills\resolve-colorist). Windows "Extract All" adds a second folder level
#    (resolve-colorist\resolve-colorist), which Claude Code never finds.
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
        Say "         Or point the skill at your own copy with the RC_FFMPEG and RC_FFPROBE environment variables."
        $Problems = $true
    }
}
if ($null -ne (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $filters = & ffmpeg -hide_banner -filters 2>$null | Out-String
    if ($filters -notmatch "libvmaf") {
        Say "Note: this ffmpeg has no libvmaf, so the optional social media check (social-qc) will skip some tests."
    }
}

# 5. Resolve's AI assistant server (optional registration)
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

# 6. Self check
$GradeLab = Join-Path $Here "grade_lab.py"
$cmds = & $VPy $GradeLab commands 2>$null
if ($LASTEXITCODE -eq 0 -and ($cmds -contains "doctor")) {
    Say ""
    Say "Running the skill's own check (grade_lab.py doctor):"
    $doc = @(& $VPy $GradeLab doctor 2>&1 | ForEach-Object { "$_" })
    if ($LASTEXITCODE -ne 0) { $Problems = $true }
    $doc | ForEach-Object { Say $_ }
    # doctor exits 0 and reports problems on its first line
    if ($doc.Count -eq 0 -or $doc[0] -notlike "DOCTOR: OK*") { $Problems = $true }
}

Say ""
Say "Next steps"
Say "1. DaVinci Resolve Studio 21.1 or newer: File > Setup AI Assistants, pick Claude Code. Then restart Claude Code."
Say "   (Or run this script again with -Mcp)"
Say "2. If Resolve tools time out: Resolve > Preferences > System > General > External scripting using: Local."
Say "3. Open your project and timeline in Resolve, start Claude Code and ask, for example:"
Say '   "Color grade my current Resolve timeline. It is an Instagram reel shot on a Sony in S-Log3."'

if ($Problems) {
    Say ""
    Say "Setup finished with warnings (see above)."
    exit 1
}
Say ""
Say "Setup finished."
exit 0
