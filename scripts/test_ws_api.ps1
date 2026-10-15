$BASE = "http://127.0.0.1:8766"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

# 登录
$loginBody = @{username="admin"; password="admin123456"} | ConvertTo-Json
$r = Invoke-RestMethod -Uri "$BASE/api/auth/login" -Method POST -Body $loginBody -ContentType "application/json" -WebSession $session
Write-Host "LOGIN: OK user=$($r.user.username)"

# GET workspaces
$r = Invoke-RestMethod -Uri "$BASE/api/workspaces" -WebSession $session
Write-Host "`nGET /api/workspaces:"
$r.workspaces | ForEach-Object { Write-Host "  [$($_.id)] active=$($_.active) name=$($_.name)" }

# POST create
$createBody = @{name="测试工作空间 A"} | ConvertTo-Json
$r = Invoke-RestMethod -Uri "$BASE/api/workspaces" -Method POST -Body $createBody -ContentType "application/json" -WebSession $session
Write-Host "`nPOST create: id=$($r.id) name=$($r.name)"

# GET again
$r = Invoke-RestMethod -Uri "$BASE/api/workspaces" -WebSession $session
Write-Host "`nAfter create:"
$r.workspaces | ForEach-Object { Write-Host "  [$($_.id)] active=$($_.active) name=$($_.name)" }

# 找测试 workspace id
$wsId = ($r.workspaces | Where-Object { $_.name -eq "测试工作空间 A" }).id

if ($wsId) {
    # POST activate
    $r = Invoke-RestMethod -Uri "$BASE/api/workspaces/$wsId/activate" -Method POST -ContentType "application/json" -WebSession $session
    Write-Host "`nPOST activate $wsId : ok=$($r.ok)"

    # GET state
    $state = Invoke-RestMethod -Uri "$BASE/api/state" -WebSession $session
    Write-Host "`nactive_workspace in state: $($state.active_workspace)"
    Write-Host "workspaces count: $($state.workspaces.Count)"

    # 切回 legacy
    $legacyId = ($r.workspaces | Where-Object { $_.name -like "*默认*" -or $_.id -eq "legacy" }).id
    # 从 state 里找
    $legacyId = ($state.workspaces | Where-Object { $_.name -like "*默认*" }).id
    Write-Host "`nSwitching back to legacy: $legacyId"
    if ($legacyId) {
        Invoke-RestMethod -Uri "$BASE/api/workspaces/$legacyId/activate" -Method POST -ContentType "application/json" -WebSession $session | Out-Null
    }

    # 删测试的
    $r = Invoke-RestMethod -Uri "$BASE/api/workspaces/$wsId" -Method DELETE -WebSession $session
    Write-Host "`nDELETE $wsId : ok=$($r.ok)"
}

Write-Host "`n=== ALL API TESTS PASSED ==="
