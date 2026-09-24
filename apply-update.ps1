$ErrorActionPreference = 'Stop'
$agentUrl = 'http://127.0.0.1:8782'
$expectedBuild = 'local-guidance-2'
$current = $null
try { $current = Invoke-RestMethod "$agentUrl/api/config" -TimeoutSec 3 } catch { }
if ($null -ne $current) {
    if ($current.app -ne 'shike-tree') { throw 'Port 8782 belongs to another application. Nothing was stopped.' }
    if ($current.build -eq $expectedBuild) { & "$PSScriptRoot\launch.ps1"; exit }
    $restarting = $false
    try {
        $result = Invoke-RestMethod "$agentUrl/api/restart" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 5
        $restarting = $result.restarting -eq $true
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -ne 404) { throw }
    }
    if (-not $restarting) {
        # Upgrade a pre-restart-endpoint server. Verify application, listener,
        # executable and idle state before terminating this one process only.
        $sessions = Invoke-RestMethod "$agentUrl/api/sessions" -TimeoutSec 3
        foreach ($session in $sessions) {
            $detail = Invoke-RestMethod "$agentUrl/api/sessions/$($session.id)" -TimeoutSec 3
            if ($detail.request_status -eq 'GENERATING') { throw 'A reply is being generated. Wait for it to finish, then apply the update again.' }
        }
        $listeners = & "$env:SystemRoot\System32\netstat.exe" -ano -p tcp
        $matchLine = $listeners | Where-Object { $_ -match '^\s*TCP\s+127\.0\.0\.1:8782\s+\S+\s+LISTENING\s+(\d+)\s*$' } | Select-Object -First 1
        if (-not $matchLine -or $matchLine -notmatch '(\d+)\s*$') { throw 'Cannot identify the old server. Nothing was stopped.' }
        $agentProcessId = [int]$Matches[1]
        $agentProcess = Get-Process -Id $agentProcessId -ErrorAction Stop
        $pythonPath = (Get-Command python -ErrorAction Stop).Source
        $pythonWindowless = Join-Path (Split-Path $pythonPath) 'pythonw.exe'
        if ($agentProcess.Path -notin @($pythonPath, $pythonWindowless)) { throw 'The listener is not the expected Python executable. Nothing was stopped.' }
        & "$env:SystemRoot\System32\taskkill.exe" /PID $agentProcessId /F
        if ($LASTEXITCODE -ne 0) { throw 'Windows refused to stop the old server. Close that Python server in Task Manager, then run start.cmd.' }
    }
    for ($attempt=0; $attempt -lt 25; $attempt++) {
        Start-Sleep -Milliseconds 200
        try {
            $running = Invoke-RestMethod "$agentUrl/api/config" -TimeoutSec 1
            if ($running.build -eq $expectedBuild) { Write-Host "Update ready: $agentUrl"; exit }
        } catch { break }
    }
}
& "$PSScriptRoot\launch.ps1"
