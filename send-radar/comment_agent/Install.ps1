[CmdletBinding()]
param([switch]$Open)
$ErrorActionPreference = 'Stop'
$base = Join-Path $env:LOCALAPPDATA 'SendRadar'
$app = Join-Path $base 'agent'
New-Item -ItemType Directory -Force -Path $app | Out-Null
if ([IO.Path]::GetFullPath($PSScriptRoot) -ne [IO.Path]::GetFullPath($app)) {
    foreach ($name in 'agent.py','store.py','review.py','youtube.py','requirements.txt','test_agent.py','README.md','Install.ps1') {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $app $name) -Force
    }
}
$python = Join-Path $base 'runtime\Scripts\python.exe'
$pythonw = Join-Path $base 'runtime\Scripts\pythonw.exe'
if (!(Test-Path $python)) {
    $systemPython = (Get-Command python.exe -ErrorAction Stop).Source
    & $systemPython -m venv (Join-Path $base 'runtime')
    if ($LASTEXITCODE) {throw 'Python virtual environment creation failed.'}
}
& $python -m pip --disable-pip-version-check install --quiet -r (Join-Path $app 'requirements.txt')
if ($LASTEXITCODE) {throw 'Dependency installation failed.'}
Push-Location $app
try {
    & $python -m unittest -q test_agent
    if ($LASTEXITCODE) {throw 'Local smoke tests failed.'}
} finally {Pop-Location}
$scriptPath = Join-Path $app 'agent.py'
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'Send Radar Inbox.lnk'))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $scriptPath + '" review'
$shortcut.WorkingDirectory = $app
$shortcut.Save()
$protocol = 'HKCU:\Software\Classes\sendradar-review'
New-Item -Path "$protocol\shell\open\command" -Force | Out-Null
Set-Item -Path $protocol -Value 'URL:Send Radar inbox'
New-ItemProperty -Path $protocol -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
Set-Item -Path "$protocol\shell\open\command" -Value ('"' + $pythonw + '" "' + $scriptPath + '" review')
$registry='HKCU:\Software\Classes\AppUserModelId\Send Radar'
New-Item -Path $registry -Force | Out-Null
New-ItemProperty -Path $registry -Name DisplayName -PropertyType String -Value 'Send Radar' -Force | Out-Null
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $scriptPath + '" sync') -WorkingDirectory $app
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 30)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Register-ScheduledTask -TaskName 'Send Radar - comment inbox' -Action $action -Trigger $trigger -Settings $settings -User $user -RunLevel Limited -Force | Out-Null
& $python $scriptPath status
if ($Open) { Start-Process -FilePath $pythonw -ArgumentList ('"' + $scriptPath + '" review') -WorkingDirectory $app }
