# Updates this machine's code, then brings automation-agent back.
#
# The automation-agent-update scheduled task runs this when Home Assistant's Update
# button is pressed. It takes no input. It pulls the branch that's checked out, so
# the robot gets main. See agent/README.md.
#
# It leaves PASS or FAIL in update_result.txt for the restarted agent to report, and
# writes the details to logs\update.log in the agent's data folder.

$ErrorActionPreference = "Stop"

$AgentTask = "automation-agent"
$StopWaitSeconds = 30

$workspace = Split-Path $PSScriptRoot -Parent
$dataRoot = if ($env:AUTOMATION_DATA_ROOT) { $env:AUTOMATION_DATA_ROOT } else { "C:\automation" }
$dataDir = Join-Path $dataRoot "agent"
$logDir = Join-Path $dataDir "logs"
$logFile = Join-Path $logDir "update.log"
$resultFile = Join-Path $dataDir "update_result.txt"

function Write-Log([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $logFile -Value "$stamp $Message"
}

# Runs a fixed command line through cmd.exe, which keeps its error output from
# becoming a PowerShell error. Returns the output, or throws if the command failed.
function Invoke-Step([string]$Command) {
    Write-Log "> $Command"
    $output = @(& cmd.exe /c "$Command 2>&1")
    $code = $LASTEXITCODE
    foreach ($line in $output) { Write-Log "  $line" }
    if ($code -ne 0) { throw "$Command failed with exit code $code" }
    return $output
}

# True once nothing from this environment is running, so its files can be replaced.
function Stop-Agent {
    Stop-ScheduledTask -TaskName $AgentTask
    $venv = Join-Path $workspace ".venv\*"
    $deadline = (Get-Date).AddSeconds($StopWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        $running = @(Get-Process | Where-Object { $_.Path -like $venv })
        if ($running.Count -eq 0) { return $true }
        Start-Sleep -Seconds 1
    }
    Write-Log "still running after $StopWaitSeconds seconds: $($running.Name -join ', ')"
    return $false
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Write-Log "update started"
$stopped = $false
$result = "FAIL"

try {
    Set-Location $workspace
    $stopped = Stop-Agent
    if (-not $stopped) { throw "the agent didn't stop; was it started by hand?" }

    $before = (Invoke-Step "git rev-parse HEAD") | Select-Object -Last 1
    Invoke-Step "git pull --ff-only" | Out-Null
    try {
        Invoke-Step "uv sync" | Out-Null
        $result = "PASS"
    } catch {
        Write-Log "ERROR: $_"
        $after = (Invoke-Step "git rev-parse HEAD") | Select-Object -Last 1
        if ($after -ne $before) {
            Write-Log "going back to $before"
            Invoke-Step "git reset --keep $before" | Out-Null
            Invoke-Step "uv sync" | Out-Null
        }
    }
} catch {
    Write-Log "ERROR: $_"
}

# Only an agent this script stopped may be started again: if one was started by hand
# it's still running, and a second one with the same MQTT client ID would fight it.
if ($stopped) {
    try {
        Set-Content -Path $resultFile -Value $result -Encoding Ascii
    } catch {
        Write-Log "ERROR: can't write the result: $_"
    }
    try {
        Start-ScheduledTask -TaskName $AgentTask
        Write-Log "agent started"
    } catch {
        Write-Log "ERROR: can't start the agent: $_"
        $result = "FAIL"
    }
}

Write-Log "update finished: $result"
if ($result -ne "PASS") { exit 1 }
