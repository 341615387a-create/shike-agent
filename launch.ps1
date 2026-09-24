$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$url = 'http://127.0.0.1:8782'
function Test-AgentReady {
    try {
        $response = Invoke-RestMethod -Uri "$url/api/config" -TimeoutSec 1
        return $response.app -eq 'shike-tree' -and $response.build -eq 'local-guidance-2'
    } catch { return $false }
}
if (-not (Test-AgentReady)) {
    $existing = $null
    try { $existing = Invoke-RestMethod -Uri "$url/api/config" -TimeoutSec 2 } catch { }
    if ($null -ne $existing) {
        if ($existing.app -ne 'shike-tree') { throw 'Port 8782 belongs to another application.' }
        & "$PSScriptRoot\apply-update.ps1"
        return
    }
    $python = Get-Command python -ErrorAction Stop
    $pythonWindowless = Join-Path (Split-Path $python.Source) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonWindowless)) { $pythonWindowless = $python.Source }
    Start-Process -FilePath $pythonWindowless -ArgumentList @('app.py', '--port', '8782') -WorkingDirectory $projectDir -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 200
        if (Test-AgentReady) { $ready = $true; break }
    }
    if (-not $ready) { throw 'Unable to start. Open start-debug.cmd to see the error.' }
}
Write-Host "Shike ready: $url"
try { Start-Process $url } catch { Write-Host "Open this address in a browser: $url" }
