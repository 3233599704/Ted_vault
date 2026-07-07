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
$env:FEISHU_ALLOWED_USERS = [Environment]::GetEnvironmentVariable(
    "FEISHU_ALLOWED_USERS", "User"
)
$env:FEISHU_NOTIFY_USERS = [Environment]::GetEnvironmentVariable(
    "FEISHU_NOTIFY_USERS", "User"
)
$env:FEISHU_STOCK_NOTIFY_USERS = [Environment]::GetEnvironmentVariable(
    "FEISHU_STOCK_NOTIFY_USERS", "User"
)
$env:MAX_CLAUDE_SECONDS = [Environment]::GetEnvironmentVariable(
    "MAX_CLAUDE_SECONDS", "User"
)
$env:STOCK_ENABLED = [Environment]::GetEnvironmentVariable(
    "STOCK_ENABLED", "User"
)
$env:STOCK_REPORT_TIME = [Environment]::GetEnvironmentVariable(
    "STOCK_REPORT_TIME", "User"
)
$env:STOCK_TIMEZONE = [Environment]::GetEnvironmentVariable(
    "STOCK_TIMEZONE", "User"
)
$env:VISION_PROVIDER = [Environment]::GetEnvironmentVariable(
    "VISION_PROVIDER", "User"
)
$env:VISION_API_KEY = [Environment]::GetEnvironmentVariable(
    "VISION_API_KEY", "User"
)
$env:VISION_API_URL = [Environment]::GetEnvironmentVariable(
    "VISION_API_URL", "User"
)
$env:VISION_MODEL = [Environment]::GetEnvironmentVariable(
    "VISION_MODEL", "User"
)
$env:TTS_API_KEY = [Environment]::GetEnvironmentVariable(
    "TTS_API_KEY", "User"
)
$env:TTS_API_URL = [Environment]::GetEnvironmentVariable(
    "TTS_API_URL", "User"
)
$env:TTS_MODEL = [Environment]::GetEnvironmentVariable(
    "TTS_MODEL", "User"
)
$env:TTS_VOICE = [Environment]::GetEnvironmentVariable(
    "TTS_VOICE", "User"
)
$env:TTS_VOICE_NAME = [Environment]::GetEnvironmentVariable(
    "TTS_VOICE_NAME", "User"
)
$env:TTS_VOICE_REFERENCE = [Environment]::GetEnvironmentVariable(
    "TTS_VOICE_REFERENCE", "User"
)
$env:TTS_MAX_CHARS = [Environment]::GetEnvironmentVariable(
    "TTS_MAX_CHARS", "User"
)
$env:TTS_PLAYBACK_SPEED = [Environment]::GetEnvironmentVariable(
    "TTS_PLAYBACK_SPEED", "User"
)
$env:TTS_DYNAMIC_STYLE = [Environment]::GetEnvironmentVariable(
    "TTS_DYNAMIC_STYLE", "User"
)
$env:TTS_DIRECTOR_MODEL = [Environment]::GetEnvironmentVariable(
    "TTS_DIRECTOR_MODEL", "User"
)

# Avoid inheriting transient IDE/sandbox proxy settings that break Feishu.
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:ALL_PROXY = $null
$env:http_proxy = $null
$env:https_proxy = $null
$env:all_proxy = $null

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
