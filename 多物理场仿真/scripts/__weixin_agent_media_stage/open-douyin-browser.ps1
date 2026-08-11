$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$profileDir = Join-Path $scriptDir "state\douyin-browser"
$chromePath = if ($env:DOUYIN_CHROME_PATH) {
  $env:DOUYIN_CHROME_PATH
} else {
  "C:\Program Files\Google\Chrome\Application\chrome.exe"
}

if (-not (Test-Path -LiteralPath $chromePath)) {
  throw "Chrome was not found: $chromePath"
}

# Stop only Chrome processes that belong to Vera's isolated Douyin profile.
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profileDir) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 800
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
Start-Process -FilePath $chromePath -ArgumentList @(
  "--user-data-dir=$profileDir",
  "--remote-debugging-port=9223",
  "--remote-debugging-address=127.0.0.1",
  "--disable-application-cache",
  "--disk-cache-size=1",
  "--media-cache-size=1",
  "--no-first-run",
  "--no-default-browser-check",
  "--new-window",
  "https://www.douyin.com/"
)

Write-Host "Vera's visible Douyin browser has been opened."
