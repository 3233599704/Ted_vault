$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

$logsDir = Join-Path $scriptDir "logs"
$stateDir = Join-Path $scriptDir "state"
$stdoutLog = Join-Path $logsDir "weixin-supervisor.stdout.log"
$stderrLog = Join-Path $logsDir "weixin-supervisor.stderr.log"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$node = (Get-Command node -ErrorAction Stop).Source
$mutexNameSource = [Convert]::ToBase64String(
    [System.Text.Encoding]::UTF8.GetBytes($scriptDir)
)
$mutexNameSafe = $mutexNameSource -replace '[^A-Za-z0-9]', ''
$mutexName = "Global\WeixinClaudeAgent-$mutexNameSafe"
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)

if (-not $createdNew) {
    Write-Output "Weixin Claude Agent is already running."
    exit 0
}

try {
    while ($true) {
        $process = Start-Process `
            -FilePath $node `
            -ArgumentList @(
                "--disable-warning=ExperimentalWarning",
                "src\supervisor.js"
            ) `
            -WorkingDirectory $scriptDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -Wait `
            -PassThru

        if ($process.ExitCode -eq 0) {
            Start-Sleep -Seconds 5
        } else {
            Start-Sleep -Seconds 15
        }
    }
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
