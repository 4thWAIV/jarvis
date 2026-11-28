<#
.SYNOPSIS
JARVIS installer for Windows 11 (Windows PowerShell 5.1 compatible).

.DESCRIPTION
From a checkout it checks the tools JARVIS needs, installs what is missing
(through winget), sets the checkout up and runs JARVIS's own preflight. On a
PC with nothing yet, the README's one line downloads this script, which
clones the repository (default %USERPROFILE%\jarvis, or -Dir) and carries on
from inside it.

  -Check       only check and report; install and change nothing
  -Yes         answer yes to the one question (unattended runs)
  -NoModels    skip the speech and embedding model downloads (Settings
               installs them later)
  -Start       start JARVIS when done and open Chrome
  -NoOpen      with -Start, do not open Chrome
  -Dir <path>  where to clone when run outside a checkout (never directly
               under a drive root: JARVIS refuses a folder other users can change)

Exit codes: 0 all good; 1 something required is missing or was declined;
            2 an install step failed; 3 JARVIS did not start; 64 bad usage.

Never asks for, reads or writes a key, token or password, and never creates
a .env file.
#>
param(
    [switch]$Check,
    [switch]$Yes,
    [switch]$NoModels,
    [switch]$Start,
    [switch]$NoOpen,
    [string]$Dir = ""
)

$RepoUrl = "https://github.com/4thWAIV/jarvis.git"
$script:FailedRequired = $false
$script:FailedStep = $false
$script:Log = ""

function Say($t) { Write-Host $t }
function Ok($t) { Write-Host "[ OK ] $t" }
function Installed($t) { Write-Host "[INSTALLED] $t" }
function Done($t) { Write-Host "[DONE] $t" }
function Optional($t) { Write-Host "[ -- ] $t" }
function Missing($name, $fix) {
    Write-Host "[MISSING] $name"
    Write-Host "          fix: $fix"
    $script:FailedRequired = $true
}
function Log($t) { if ($script:Log) { Add-Content -LiteralPath $script:Log -Value $t -Encoding UTF8 } }

# A command's output goes to install.log; on failure show the step, the
# command and the last lines. Returns $true when it exited 0.
function Invoke-Logged([string]$Label, [string]$Exe, [string[]]$Arguments) {
    Log "=== ${Label}: $Exe $($Arguments -join ' ')"
    $out = @()
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } } catch { $out = @("$_"); $global:LASTEXITCODE = 1 }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    foreach ($line in $out) { Log $line }
    if ($code -ne 0) {
        Write-Host "[FAILED] $Label"
        Write-Host "  command: $Exe $($Arguments -join ' ')"
        Write-Host "  last output:"
        $out | Select-Object -Last 15 | ForEach-Object { Write-Host "    $_" }
        $script:FailedStep = $true
        return $false
    }
    return $true
}

# Run a command quietly and return its first line of output ("" on failure).
function Get-Output([string]$Exe, [string[]]$Arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $o = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } } catch { $o = @() }
    $script:LastExit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($o) { return ($o -join "`n") }
    return ""
}

function Get-Version([string]$Text) {
    $m = [regex]::Match($Text, '\d+(\.\d+)+')
    if ($m.Success) { return $m.Value }
    return ""
}

function Test-VersionAtLeast([string]$Found, [string]$Minimum) {
    if (-not $Found) { return $false }
    try {
        $a = [version]($Found + $(if ($Found.Split('.').Count -lt 3) { ".0" } else { "" }))
        $b = [version]$Minimum
        return $a -ge $b
    } catch { return $false }
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $have = @($env:Path -split ';' | Where-Object { $_ })
    foreach ($entry in (("$machine;$user") -split ';')) {
        if ($entry -and ($have -notcontains $entry)) { $env:Path = "$env:Path;$entry"; $have += $entry }
    }
}

function Test-Command([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Ask([string]$Question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) { return $false }
    try { $a = Read-Host "$Question [y/N]" } catch { return $false }
    return ($a -match '^(y|yes)$')
}

# -- git, and the bootstrap when not inside a checkout --------------------------------

function Find-Git {
    if (Test-Command "git") { return (Get-Command git).Source }
    foreach ($p in @("$env:ProgramFiles\Git\cmd\git.exe", "${env:ProgramFiles(x86)}\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe")) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}

function Test-Winget { return (Test-Command "winget") }

function Install-WithWinget([string]$Label, [string]$Id) {
    $ok = Invoke-Logged $Label "winget" @("install", "-e", "--id", $Id, "--silent", "--accept-package-agreements", "--accept-source-agreements")
    Update-SessionPath
    if ($ok) { Installed $Label }
    return $ok
}

$here = ""
if ($PSScriptRoot) { $here = $PSScriptRoot }
elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
function Test-Checkout([string]$Path) {
    return ($Path -and (Test-Path -LiteralPath (Join-Path $Path "jarvis\background.py")) -and (Test-Path -LiteralPath (Join-Path $Path "requirements.txt")))
}

if (-not (Test-Checkout $here)) {
    Say "JARVIS installer: not run from a checkout; cloning first."
    $target = $Dir
    if (-not $target) { $target = Join-Path $env:USERPROFILE "jarvis" }
    $target = [IO.Path]::GetFullPath($target)
    $parent = Split-Path -Parent $target
    if ($parent -eq [IO.Path]::GetPathRoot($target)) {
        Write-Host "Not directly under a drive root ($target): JARVIS refuses a folder other users can change. Use -Dir under your user profile."
        exit 64
    }
    $git = Find-Git
    if (-not $git) {
        Missing "Git for Windows" "winget install -e --id Git.Git"
        if ($Check) { exit 1 }
        if (-not (Test-Winget)) {
            Say "winget is missing: install 'App Installer' from the Microsoft Store, then run this again."
            exit 1
        }
        if (-not (Ask "Install Git for Windows with winget?")) { exit 1 }
        $script:FailedRequired = $false
        if (-not (Install-WithWinget "Git for Windows" "Git.Git")) { exit 2 }
        $git = Find-Git
        if (-not $git) { Say "Git was installed but is not on PATH yet; open a new PowerShell and run this again."; exit 1 }
    }
    if (Test-Checkout $target) {
        Ok "checkout already at $target"
    } elseif ((Test-Path -LiteralPath $target) -and (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1)) {
        Write-Host "$target exists and is not a JARVIS checkout. Choose another folder with -Dir."
        exit 1
    } elseif ($Check) {
        Missing "JARVIS checkout at $target" "run the installer without -Check to clone it"
        exit 1
    } else {
        & $git clone $RepoUrl $target
        if ($LASTEXITCODE -ne 0) { Write-Host "git clone failed"; exit 2 }
        Installed "cloned $RepoUrl into $target"
    }
    $forward = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $target "install.ps1"))
    if ($Check) { $forward += "-Check" }
    if ($Yes) { $forward += "-Yes" }
    if ($NoModels) { $forward += "-NoModels" }
    if ($Start) { $forward += "-Start" }
    if ($NoOpen) { $forward += "-NoOpen" }
    & powershell.exe @forward
    exit $LASTEXITCODE
}

Set-Location -LiteralPath $here
$Root = $here
if (-not $Check) {
    $script:Log = Join-Path $Root "install.log"
    Log ""
    Log "##### install.ps1 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
}

Say "JARVIS installer (Windows)"
if ($Check) { Say "Check only: nothing will be installed or changed." }
Say ""

# -- 1. the tools -----------------------------------------------------------------------

$toInstall = New-Object System.Collections.ArrayList
$need = @{}

function Want([string]$Key, [string]$What) { $need[$Key] = $true; [void]$toInstall.Add($What) }

$hasWinget = Test-Winget
if ($hasWinget) { Ok "winget $(Get-Version (Get-Output 'winget' @('--version')))" }
else { Optional "winget not found (needed only to install missing tools; get 'App Installer' from the Microsoft Store)" }

$git = Find-Git
if ($git) { Ok "Git for Windows $(Get-Version (Get-Output $git @('--version')))" }
else { Want "git" "Git for Windows (winget Git.Git)" }

# openssl comes with Git for Windows
$opensslFound = $false
if (Test-Command "openssl") { $opensslFound = $true }
elseif ($git) {
    $gitRoot = Split-Path -Parent (Split-Path -Parent $git)
    if (Test-Path -LiteralPath (Join-Path $gitRoot "usr\bin\openssl.exe")) { $opensslFound = $true }
}
if ($opensslFound) { Ok "openssl (with Git for Windows)" }
elseif ($git) { Missing "openssl" "reinstall Git for Windows (winget install -e --id Git.Git); its usr\bin\openssl.exe is what JARVIS uses" }

# Python 3.12 (py -3.12), or newer
function Find-Python {
    $candidates = @(@("py", "-3.12"), @("py", "-3.13"), @("python"))
    foreach ($c in $candidates) {
        if (-not (Test-Command $c[0])) { continue }
        $exe = $c[0]; $args2 = @()
        if ($c.Count -gt 1) { $args2 = @($c[1]) }
        $path = Get-Output $exe ($args2 + @("-c", "import sys; print(sys.executable)"))
        $ver = Get-Version (Get-Output $exe ($args2 + @("--version")))
        if ($path -and (Test-Path -LiteralPath $path.Trim()) -and (Test-VersionAtLeast $ver "3.12.0")) {
            return @{ Path = $path.Trim(); Version = $ver }
        }
    }
    return $null
}
$python = Find-Python
if ($python) { Ok "Python $($python.Version) ($($python.Path))" }
else { Want "python" "Python 3.12 (winget Python.Python.3.12)" }

# Node.js 22 with npm
$nodeOk = $false
if ((Test-Command "node") -and (Test-Command "npm.cmd")) {
    $nv = Get-Version (Get-Output "node" @("--version"))
    if (Test-VersionAtLeast $nv "22.0.0") {
        $nodeOk = $true
        Ok "Node.js $nv, npm $(Get-Version (Get-Output 'npm.cmd' @('--version')))"
    }
}
if (-not $nodeOk) { Want "node" "Node.js 22 LTS with npm (winget OpenJS.NodeJS.22)" }

# Google Chrome
function Find-Chrome {
    foreach ($p in @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}
$chrome = Find-Chrome
if ($chrome) { Ok "Google Chrome $((Get-Item -LiteralPath $chrome).VersionInfo.ProductVersion) ($chrome)" }
else { Want "chrome" "Google Chrome (winget Google.Chrome)" }

# Claude Code 2.1.224 or newer, as claude.exe (JARVIS refuses a bare .cmd/.bat launcher)
$minClaude = "2.1.224"
function Find-ClaudeExe {
    $found = Get-Command claude -ErrorAction SilentlyContinue
    if ($found) {
        if ($found.Source -match '\.exe$') { return $found.Source }
        # npm's claude.cmd: the package ships bin\claude.exe beside the shim's node_modules
        $beside = Join-Path (Split-Path -Parent $found.Source) "node_modules\@anthropic-ai\claude-code\bin\claude.exe"
        if (Test-Path -LiteralPath $beside) { return $beside }
    }
    foreach ($p in @("$env:USERPROFILE\.local\bin\claude.exe", "$env:LOCALAPPDATA\Microsoft\WinGet\Links\claude.exe")) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}
$claudeExe = Find-ClaudeExe
$claudeVer = ""
if ($claudeExe) { $claudeVer = Get-Version (Get-Output $claudeExe @("--version")) }
if ($claudeExe -and (Test-VersionAtLeast $claudeVer $minClaude)) { Ok "Claude Code $claudeVer ($claudeExe)" }
elseif ($claudeExe) { Want "claude" "Claude Code $minClaude or newer (found $claudeVer; winget upgrade Anthropic.ClaudeCode)" }
else { Want "claude" "Claude Code (winget Anthropic.ClaudeCode, else npm install -g @anthropic-ai/claude-code)" }

# Codex is optional
if (Test-Command "codex") { Ok "Codex CLI $(Get-Version (Get-Output 'codex' @('--version'))) (optional)" }
else { Optional "Codex CLI not installed (optional; npm.cmd install -g @openai/codex)" }

# -- 2. install what is missing ------------------------------------------------------------

if ($toInstall.Count -gt 0) {
    Say ""
    Say "Missing or too old:"
    foreach ($w in $toInstall) { Say "  - $w" }
    if ($Check) {
        if ($need["git"]) { Missing "Git for Windows" "winget install -e --id Git.Git" }
        if ($need["python"]) { Missing "Python 3.12" "winget install -e --id Python.Python.3.12" }
        if ($need["node"]) { Missing "Node.js 22 with npm" "winget install -e --id OpenJS.NodeJS.22" }
        if ($need["chrome"]) { Missing "Google Chrome" "winget install -e --id Google.Chrome" }
        if ($need["claude"]) { Missing "Claude Code $minClaude or newer" "winget install -e --id Anthropic.ClaudeCode" }
    } else {
        if (-not $hasWinget) {
            Say ""
            Say "winget is missing. Install 'App Installer' from the Microsoft Store, then run this again."
            exit 1
        }
        Say ""
        if (-not (Ask "Install these now?")) {
            Say "Nothing installed. Run again with -Yes, or install them yourself."
            exit 1
        }
        Say ""
        if ($need["git"]) { [void](Install-WithWinget "Git for Windows" "Git.Git") }
        if ($need["python"]) { [void](Install-WithWinget "Python 3.12" "Python.Python.3.12") }
        if ($need["node"]) {
            if (-not (Install-WithWinget "Node.js 22" "OpenJS.NodeJS.22")) {
                $script:FailedStep = $false
                [void](Install-WithWinget "Node.js LTS" "OpenJS.NodeJS.LTS")
            }
        }
        if ($need["chrome"]) { [void](Install-WithWinget "Google Chrome" "Google.Chrome") }
        if ($need["claude"]) {
            $script:FailedStep = $false
            if (-not (Install-WithWinget "Claude Code" "Anthropic.ClaudeCode")) {
                $script:FailedStep = $false
                if ((Test-Command "npm.cmd") -and (Invoke-Logged "Claude Code (npm)" "npm.cmd" @("install", "-g", "@anthropic-ai/claude-code"))) {
                    Update-SessionPath; Installed "Claude Code (npm)"
                } else { Say "  fix: winget install -e --id Anthropic.ClaudeCode, or npm.cmd install -g @anthropic-ai/claude-code" }
            }
        }
        # Look again now that things may exist.
        $git = Find-Git
        $python = Find-Python
        $chrome = Find-Chrome
        $claudeExe = Find-ClaudeExe
        if (-not $git) { Missing "Git for Windows" "open a new PowerShell and run this again (PATH changed)" }
        if (-not $python) { Missing "Python 3.12" "open a new PowerShell and run this again (PATH changed)" }
        if (-not ((Test-Command "node") -and (Test-VersionAtLeast (Get-Version (Get-Output "node" @("--version"))) "22.0.0"))) { Missing "Node.js 22 with npm" "open a new PowerShell and run this again (PATH changed)" }
        if (-not $chrome) { Missing "Google Chrome" "winget install -e --id Google.Chrome" }
        if (-not $claudeExe) { Missing "Claude Code" "winget install -e --id Anthropic.ClaudeCode" }
        # The things above that were not missing before are not failures now.
        if ($script:FailedStep) { $script:FailedRequired = $true }
    }
}

if ($script:FailedStep -or ($script:FailedRequired -and -not $Check)) {
    Say ""
    Say "A required tool is still missing or failed; the checkout was not set up."
    if ($script:FailedStep) { exit 2 } else { exit 1 }
}

# -- 3. the checkout ---------------------------------------------------------------------------

Say ""
Say "Checkout: $Root"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
$ready = $true
$npm = "npm.cmd"

function Invoke-PyQuiet([string[]]$Arguments) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $venvPy @Arguments 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}

if (Test-Path -LiteralPath $venvPy) {
    $vv = Get-Version (Get-Output $venvPy @("--version"))
    if (Test-VersionAtLeast $vv "3.12.0") { Ok ".venv (Python $vv)" }
    else { Missing ".venv uses Python $vv, older than 3.12" "remove the folder .venv and run this again"; $ready = $false }
} elseif ($Check -or -not $python) {
    Missing ".venv" "run .\install.ps1 (py -3.12 -m venv .venv)"
    $ready = $false
} else {
    if (Invoke-Logged "create .venv" $python.Path @("-m", "venv", (Join-Path $Root ".venv"))) { Done ".venv created with Python $($python.Version)" }
    else { $ready = $false }
}

if ($ready) {
    # Python dependencies
    if ((Invoke-PyQuiet @("-m", "jarvis.install_check", "stamp-ok", "pip")) -eq 0) { Ok "Python dependencies already installed" }
    elseif ($Check) { Missing "Python dependencies" "$venvPy -m pip install -r requirements.txt" }
    elseif (Invoke-Logged "pip install -r requirements.txt" $venvPy @("-m", "pip", "install", "-r", "requirements.txt")) {
        [void](Invoke-PyQuiet @("-m", "jarvis.install_check", "stamp-set", "pip")); Done "Python dependencies installed"
    }

    # Frontend dependencies
    if ((Invoke-PyQuiet @("-m", "jarvis.install_check", "stamp-ok", "npm")) -eq 0) { Ok "Frontend dependencies already installed" }
    elseif ($Check) { Missing "Frontend dependencies" "npm.cmd --prefix frontend ci" }
    elseif (Invoke-Logged "npm.cmd --prefix frontend ci" $npm @("--prefix", "frontend", "ci")) {
        [void](Invoke-PyQuiet @("-m", "jarvis.install_check", "stamp-set", "npm")); Done "Frontend dependencies installed"
    }

    # Development certificate
    if ((Invoke-PyQuiet @("-m", "jarvis.install_check", "done", "certs")) -eq 0) { Ok "Development certificate (cert.pem, key.pem)" }
    elseif ($Check) { Missing "Development certificate" "$venvPy -m jarvis.background certs" }
    elseif (Invoke-Logged "development certificate" $venvPy @("-m", "jarvis.background", "certs")) { Done "Development certificate made" }

    # Playwright's Chromium
    if ((Invoke-PyQuiet @("-m", "jarvis.install_check", "done", "playwright")) -eq 0) { Ok "Playwright Chromium" }
    elseif ($Check) { Missing "Playwright Chromium" "$venvPy -m playwright install chromium" }
    elseif (Invoke-Logged "playwright install chromium" $venvPy @("-m", "playwright", "install", "chromium")) { Done "Playwright Chromium installed" }

    # Models: large, optional
    if ($NoModels) { Optional "Speech and embedding models skipped (-NoModels); Settings installs them later" }
    else {
        $sizes = (Get-Output $venvPy @("-m", "jarvis.install_check", "models")) -replace "`r?`n", "; "
        if ((Invoke-PyQuiet @("-m", "jarvis.install_check", "done", "speech")) -eq 0) { Ok "English speech model" }
        elseif ($Check) { Optional "English speech model not installed (optional: $venvPy scripts\setup_speech_input.py)" }
        else {
            Say "Downloading: $sizes"
            if (Invoke-Logged "speech model" $venvPy @("scripts\setup_speech_input.py")) { Done "English speech model installed" }
        }
        if ((Invoke-PyQuiet @("-m", "jarvis.install_check", "done", "embedding")) -eq 0) { Ok "Embedding model" }
        elseif ($Check) { Optional "Embedding model not installed (optional: $venvPy scripts\setup_embedding_model.py)" }
        elseif (Invoke-Logged "embedding model" $venvPy @("scripts\setup_embedding_model.py")) { Done "Embedding model installed" }
    }

    # -- 4. preflight ----------------------------------------------------------------------
    Say ""
    Say "JARVIS preflight (warnings are things to do, not failures):"
    $pf = Get-Output $venvPy @("-m", "jarvis.install_check", "preflight")
    foreach ($line in ($pf -split "`n")) { if ($line.Trim()) { Say "  $line" } }
    Log "=== preflight"; Log $pf

    # The talk-to-jarvis skill. It needs a Claude Code or Codex home, which a first `claude`
    # run makes (preflight's login check is one), so it comes after preflight.
    $skill = Invoke-PyQuiet @("-m", "jarvis.install_check", "done", "skill")
    if ($skill -eq 0) { Ok "talk-to-jarvis skill" }
    elseif ($skill -eq 2) { Optional "talk-to-jarvis skill: no Claude Code or Codex home yet; run 'claude' once, then run this again" }
    elseif ($Check) { Optional "talk-to-jarvis skill not installed (optional: $venvPy -m jarvis.session_skill install)" }
    elseif (Invoke-Logged "session skill" $venvPy @("-m", "jarvis.session_skill", "install")) { Done "talk-to-jarvis skill installed" }
}

# -- 5. what only the person can do ----------------------------------------------------------------

Say ""
Say "Left for you (this script never handles these):"
Say "  1. Sign in to Claude Code: run 'claude', then '/login' with your Claude subscription."
Say "     JARVIS never uses an API key."
Say "  2. Fish Audio key: open Settings, Voice in JARVIS and enter it there (from fish.audio)."
Say "  3. Chrome: open http://localhost:5173 and allow the microphone for that address."

# -- 6. start -----------------------------------------------------------------------------------------

if ((-not $Check) -and (-not $script:FailedStep) -and (-not $script:FailedRequired)) {
    if ((-not $Start) -and (-not $Yes) -and [Environment]::UserInteractive) {
        if (Ask "Start JARVIS now?") { $Start = $true }
    }
    if ($Start) {
        Say ""
        $started = $false
        if ((Invoke-PyQuiet @("-m", "jarvis.background", "status")) -eq 0) { Ok "JARVIS is already running"; $started = $true }
        else {
            Log "=== jarvis.background start"
            $o = Get-Output $venvPy @("-m", "jarvis.background", "start")
            Say $o; Log $o
            if ($script:LastExit -eq 0) { $started = $true }
            else {
                Write-Host "[FAILED] start"
                Write-Host "  command: $venvPy -m jarvis.background logs backend"
                (Get-Output $venvPy @("-m", "jarvis.background", "logs", "backend", "-n", "15")) -split "`n" | ForEach-Object { Write-Host "    $_" }
                exit 3
            }
        }
        if ($started -and -not $NoOpen) {
            $c = Find-Chrome
            if ($c) { Start-Process -FilePath $c -ArgumentList "http://localhost:5173" } else { Start-Process "http://localhost:5173" }
        }
        Say "  status: $venvPy -m jarvis.background status"
        Say "  stop:   $venvPy -m jarvis.background stop"
        Say "  logs:   $venvPy -m jarvis.background logs backend   (or vite)"
    }
}

# -- summary ---------------------------------------------------------------------------------------------

Say ""
if ($script:FailedStep) { Say "Summary: a step failed. Details are in $Root\install.log."; exit 2 }
if ($script:FailedRequired) { Say "Summary: something required is missing (see MISSING above)."; exit 1 }
if ($Check) { Say "Summary: everything required is in place." }
else { Say "Summary: JARVIS is installed. Open http://localhost:5173 in Chrome once it is running." }
exit 0
