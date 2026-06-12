$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$botScript = Join-Path $scriptDir "feishu-claude-bot.py"
$stdoutLog = Join-Path $scriptDir "feishu-bot.stdout.log"
$stderrLog = Join-Path $scriptDir "feishu-bot.stderr.log"
$python = "C:\Windows\py.exe"

# Scheduled tasks can inherit an old environment block, so read current values.
$env:FEISHU_APP_ID = [Environment]::GetEnvironmentVariable(
    "FEISHU_APP_ID", "User"
)
$env:FEISHU_APP_SECRET = [Environment]::GetEnvironmentVariable(
    "FEISHU_APP_SECRET", "User"
)

Set-Location -LiteralPath $scriptDir

while ($true) {
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @($botScript) `
        -WorkingDirectory $scriptDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -Wait `
        -PassThru

    # 0 means a deliberate stop; 2 means another instance or bad configuration.
    if ($process.ExitCode -eq 0 -or $process.ExitCode -eq 2) {
        exit $process.ExitCode
    }

    Start-Sleep -Seconds 15
}
