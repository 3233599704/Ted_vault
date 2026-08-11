param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("protect", "unprotect")]
    [string]$Mode
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$inputText = [Console]::In.ReadToEnd()
$entropy = [System.Text.Encoding]::UTF8.GetBytes("VeraOutlookBridge/v1")

if ($Mode -eq "protect") {
    $plain = [System.Text.Encoding]::UTF8.GetBytes($inputText)
    $cipher = [System.Security.Cryptography.ProtectedData]::Protect(
        $plain,
        $entropy,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    [Console]::Out.Write([Convert]::ToBase64String($cipher))
    exit 0
}

$cipher = [Convert]::FromBase64String($inputText.Trim())
$plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
    $cipher,
    $entropy,
    [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)
[Console]::Out.Write([System.Text.Encoding]::UTF8.GetString($plain))
