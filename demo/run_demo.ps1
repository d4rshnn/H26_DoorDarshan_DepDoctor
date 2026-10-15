<#
.SYNOPSIS
    Reproducible end-to-end demonstration script for DepDoctor.

.DESCRIPTION
    Runs the full DepDoctor pipeline (analyze + finish) on a target repository,
    proves the target is completely unmodified via SHA-256 fingerprinting, displays
    the generated capsule summary, and opens the HTML report in the default browser.

.PARAMETER Target
    Path to the target repository to inspect (default: D:\Hackathons\LinkWatch_target).

.PARAMETER Out
    Directory where output artifacts and capsule files will be stored (default: $env:TEMP\depdoctor_demo).

.PARAMETER Python
    Python version launcher string to invoke (default: 3.12).

.PARAMETER SkipPreflight
    Switch to bypass environment preflight checks.

.EXAMPLE
    .\demo\run_demo.ps1 -Target "D:\Hackathons\LinkWatch_target"

.EXAMPLE
    .\demo\run_demo.ps1 -Target "D:\Hackathons\LinkWatch_target" -SkipPreflight
#>

[CmdletBinding()]
param(
    [string]$Target = "D:\Hackathons\LinkWatch_target",
    [string]$Out = "$env:TEMP\depdoctor_demo",
    [string]$Python = "3.12",
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param(
        [string]$StepNumber,
        [string]$Title,
        [string]$Description
    )
    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host " [STEP $StepNumber] $Title" -ForegroundColor Yellow
    if ($Description) {
        Write-Host "          $Description" -ForegroundColor Gray
    }
    Write-Host "================================================================================" -ForegroundColor Cyan
}

function Write-CheckOk {
    param([string]$Message)
    Write-Host "  [OK] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-CheckFail {
    param([string]$Message, [string]$Hint)
    Write-Host "  [FAIL] " -ForegroundColor Red -NoNewline
    Write-Host $Message
    if ($Hint) {
        Write-Host "         Fix: $Hint" -ForegroundColor Magenta
    }
}

function Get-TargetFingerprint {
    param([string]$Path)
    if (-not (Test-Path -Path $Path -PathType Container)) {
        throw "Target folder '$Path' does not exist."
    }
    $resolvedTarget = (Resolve-Path -Path $Path).Path
    $files = Get-ChildItem -Path $resolvedTarget -Recurse -File | Where-Object {
        $_.FullName -notmatch '[\\/]\.git([\\/]|$)'
    } | Sort-Object {
        $_.FullName.Substring($resolvedTarget.Length).TrimStart('\', '/') -replace '\\', '/'
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $memoryStream = New-Object System.IO.MemoryStream
    $streamWriter = New-Object System.IO.StreamWriter($memoryStream, [System.Text.Encoding]::UTF8)

    $fileHasher = [System.Security.Cryptography.SHA256]::Create()
    foreach ($file in $files) {
        $relPath = $file.FullName.Substring($resolvedTarget.Length).TrimStart('\', '/') -replace '\\', '/'
        $fileStream = [System.IO.File]::OpenRead($file.FullName)
        $fileHashBytes = $fileHasher.ComputeHash($fileStream)
        $fileStream.Dispose()
        $fileHash = ($fileHashBytes | ForEach-Object { $_.ToString("x2") }) -join ""
        $streamWriter.WriteLine("${relPath}:${fileHash}")
    }
    $fileHasher.Dispose()
    $streamWriter.Flush()
    $memoryStream.Position = 0
    $overallHashBytes = $sha256.ComputeHash($memoryStream)
    $overallHash = ($overallHashBytes | ForEach-Object { $_.ToString("x2") }) -join ""

    $streamWriter.Dispose()
    $memoryStream.Dispose()
    $sha256.Dispose()

    return $overallHash
}

$totalStopwatch = [System.Diagnostics.Stopwatch]::StartNew()

Write-Host "--------------------------------------------------------------------------------" -ForegroundColor DarkCyan
Write-Host " DepDoctor Live Demo Runner" -ForegroundColor White
Write-Host " Target: $Target" -ForegroundColor Gray
Write-Host " Output: $Out" -ForegroundColor Gray
Write-Host " Python: $Python" -ForegroundColor Gray
Write-Host "--------------------------------------------------------------------------------" -ForegroundColor DarkCyan

# ------------------------------------------------------------------------------
# STEP 1: Preflight checks
# ------------------------------------------------------------------------------
Write-Step "1" "Preflight Environment Checks" "Verifies Python runtime, target files, local Ollama model, and tool imports."

$preflightPassed = $true

if ($SkipPreflight) {
    Write-Host "  Preflight checks skipped by user (-SkipPreflight)." -ForegroundColor Yellow
} else {
    # 1.1 Python version check
    $pyCheck = & py "-$Python" --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-CheckOk "Python launcher found: $pyCheck"
    } else {
        Write-CheckFail "Python $Python is not available via py launcher." "Install Python $Python from python.org or verify with 'py -$Python --version'."
        $preflightPassed = $false
    }

    # 1.2 Target folder contents check
    if (Test-Path -Path $Target -PathType Container) {
        $lwDir = Join-Path -Path $Target -ChildPath "linkwatch"
        $reqFile = Join-Path -Path $Target -ChildPath "requirements.txt"
        if ((Test-Path -Path $lwDir) -and (Test-Path -Path $reqFile)) {
            Write-CheckOk "Target repository exists and contains linkwatch\ and requirements.txt."
        } else {
            Write-CheckFail "Target directory exists but is missing linkwatch\ or requirements.txt." "Provide a valid target clone containing linkwatch\ and requirements.txt."
            $preflightPassed = $false
        }
    } else {
        Write-CheckFail "Target directory '$Target' does not exist." "Clone the target repository outside this project (e.g. to $Target)."
        $preflightPassed = $false
    }

    # 1.3 Ollama server and gemma4:e4b model check
    try {
        $tagsResponse = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 5 -ErrorAction Stop
        $modelFound = $tagsResponse.models | Where-Object { $_.name -eq "gemma4:e4b" -or $_.model -eq "gemma4:e4b" }
        if ($modelFound) {
            Write-CheckOk "Local Ollama server is responding and has model 'gemma4:e4b' ready."
        } else {
            $availableNames = ($tagsResponse.models | ForEach-Object { $_.name }) -join ", "
            Write-CheckFail "Ollama is running, but model 'gemma4:e4b' was not found (available: $availableNames)." "Run 'ollama cp gemma4:latest gemma4:e4b' or pull 'gemma4:e4b'."
            $preflightPassed = $false
        }
    } catch {
        Write-CheckFail "Local Ollama server is not reachable at http://localhost:11434 within 5s." "Start Ollama by running 'ollama serve' in a separate terminal."
        $preflightPassed = $false
    }

    # 1.4 Tool module importability check
    $toolHelp = & py "-$Python" -m tool --help 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-CheckOk "DepDoctor 'tool' module is importable and responsive."
    } else {
        Write-CheckFail "Cannot run 'py -$Python -m tool --help' from the current working directory." "Run this script from the root of the DepDoctor repository."
        $preflightPassed = $false
    }

    if (-not $preflightPassed) {
        Write-Host ""
        Write-Host "Preflight validation failed. Aborting demo execution." -ForegroundColor Red
        $totalStopwatch.Stop()
        exit 1
    }
}

# ------------------------------------------------------------------------------
# STEP 2: Fingerprint the target BEFORE
# ------------------------------------------------------------------------------
Write-Step "2" "Fingerprint Target (Before)" "Computes a deterministic SHA-256 hash of all files in the target before any operation."

try {
    $beforeFingerprint = Get-TargetFingerprint -Path $Target
    Write-Host "  Target SHA-256 (before): $beforeFingerprint" -ForegroundColor Green
} catch {
    Write-Host "  Error computing target fingerprint: $_" -ForegroundColor Red
    $totalStopwatch.Stop()
    exit 1
}

# ------------------------------------------------------------------------------
# STEP 3: Run analyze
# ------------------------------------------------------------------------------
Write-Step "3" "Run Analyze Stage" "Scans dependencies, runs target tests in an isolated sandbox, and records baseline failures."

if (Test-Path -Path $Out) {
    Write-Host "  Cleaning existing output directory: $Out" -ForegroundColor Gray
    Remove-Item -Path $Out -Recurse -Force
}

$analyzeArgs = @("-$Python", "-m", "tool", "analyze", $Target, "--out-dir", $Out)
Write-Host "  Command: py $($analyzeArgs -join ' ')" -ForegroundColor DarkGray
& py $analyzeArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  [FAIL] Analyze stage failed with exit code $LASTEXITCODE." -ForegroundColor Red
    $totalStopwatch.Stop()
    exit 1
}
Write-Host "  [OK] Analyze stage completed successfully." -ForegroundColor Green

# ------------------------------------------------------------------------------
# STEP 4: Run finish
# ------------------------------------------------------------------------------
Write-Step "4" "Run Finish Stage (Propose, Validate, Verify, Capsule)" "Prompts local AI for fixes, verifies them in clean sandboxes, and compiles reports."

$finishStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$finishArgs = @("-$Python", "-m", "tool", "finish", $Target, "--out-dir", $Out)
Write-Host "  Command: py $($finishArgs -join ' ')" -ForegroundColor DarkGray
& py $finishArgs
$finishExit = $LASTEXITCODE
$finishStopwatch.Stop()

Write-Host ("  Finish stage execution time: {0:mm\:ss\.ff}" -f $finishStopwatch.Elapsed) -ForegroundColor Gray

if ($finishExit -ne 0) {
    Write-Host ""
    Write-Host "  [FAIL] Finish stage failed with exit code $finishExit." -ForegroundColor Red
    $totalStopwatch.Stop()
    exit 1
}
Write-Host "  [OK] Finish stage completed successfully." -ForegroundColor Green

# ------------------------------------------------------------------------------
# STEP 5: Fingerprint the target AFTER
# ------------------------------------------------------------------------------
Write-Step "5" "Verify Target Immutability" "Recalculates target SHA-256 fingerprint to guarantee zero modifications occurred."

$afterFingerprint = Get-TargetFingerprint -Path $Target
Write-Host "  Target SHA-256 (before): $beforeFingerprint" -ForegroundColor Gray
Write-Host "  Target SHA-256 (after):  $afterFingerprint" -ForegroundColor Gray

if ($beforeFingerprint -eq $afterFingerprint) {
    Write-Host "  Target repository unchanged: YES" -ForegroundColor Green
} else {
    Write-Host "  Target repository unchanged: NO (Target was modified!)" -ForegroundColor Red
    $totalStopwatch.Stop()
    exit 1
}

# ------------------------------------------------------------------------------
# STEP 6: Display capsule summary and open HTML report
# ------------------------------------------------------------------------------
Write-Step "6" "Present Verification Artifacts" "Displays capsule summary and launches the interactive visual report."

$capsuleMd = Join-Path -Path $Out -ChildPath "capsule.md"
$capsuleJson = Join-Path -Path $Out -ChildPath "capsule.json"
$capsuleHtml = Join-Path -Path $Out -ChildPath "capsule.html"

Write-Host "  Generated Artifacts:" -ForegroundColor White
Write-Host "    - Markdown Capsule: $capsuleMd" -ForegroundColor Gray
Write-Host "    - JSON Capsule:     $capsuleJson" -ForegroundColor Gray
Write-Host "    - HTML Report:      $capsuleHtml" -ForegroundColor Gray

if (Test-Path -Path $capsuleMd) {
    Write-Host ""
    Write-Host "------------------- Capsule Markdown Preview (First 40 Lines) -------------------" -ForegroundColor DarkCyan
    Get-Content -Path $capsuleMd -TotalCount 40
    Write-Host "--------------------------------------------------------------------------------" -ForegroundColor DarkCyan
} else {
    Write-Host "  Warning: capsule.md was not found in $Out." -ForegroundColor Yellow
}

if (Test-Path -Path $capsuleHtml) {
    Write-Host ""
    Write-Host "  Launching capsule.html in default browser..." -ForegroundColor Green
    Start-Process -FilePath $capsuleHtml
} else {
    Write-Host "  Warning: capsule.html was not found in $Out." -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# STEP 7: Total elapsed time & summary
# ------------------------------------------------------------------------------
$totalStopwatch.Stop()
Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host (" Demo finished successfully! Total elapsed time: {0:mm\:ss\.ff}" -f $totalStopwatch.Elapsed) -ForegroundColor Green
Write-Host "================================================================================" -ForegroundColor Cyan
exit 0
