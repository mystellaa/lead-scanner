$ErrorActionPreference = 'Stop'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutFolder = Join-Path $desktopPath 'Lead Scanner'
New-Item -ItemType Directory -Path $shortcutFolder -Force | Out-Null
$shortcutShell = New-Object -ComObject WScript.Shell
$launcher = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.bat' | Select-Object -First 1
if (-not $launcher) { throw 'Launcher not found.' }
$entries = @(
    @{Name='01 Start.lnk'; Target=$launcher.FullName; Arguments=''},
    @{Name='02 Project.lnk'; Target=(Join-Path $env:WINDIR 'explorer.exe'); Arguments=('"' + $PSScriptRoot + '"')},
    @{Name='03 Data.lnk'; Target=(Join-Path $env:WINDIR 'explorer.exe'); Arguments=('"' + (Join-Path $PSScriptRoot 'data') + '"')},
    @{Name='04 GitHub.url'; Target='https://github.com/mystellaa/lead-scanner'; Arguments=''}
)
foreach ($entry in $entries) {
    $linkPath = Join-Path $shortcutFolder $entry.Name
    if (Test-Path -LiteralPath $linkPath) { continue }
    $shortcut = $shortcutShell.CreateShortcut($linkPath)
    $shortcut.TargetPath = $entry.Target
    if ($entry.Name.EndsWith('.lnk')) {
        $shortcut.Arguments = $entry.Arguments
        $shortcut.WorkingDirectory = $PSScriptRoot
    }
    $shortcut.Save()
}
Write-Output $shortcutFolder
