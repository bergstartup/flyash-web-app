# Put an "SMCP Kallakudi" icon on the Windows desktop that starts the app in
# WSL and opens it in the browser.
#
# Run it from the project as Windows sees it inside WSL, e.g. by
# double-clicking install-desktop-icon.bat in
#   \\wsl.localhost\Ubuntu\home\<you>\webapp\scripts\windows
# The WSL distro and project folder are read from that path.

$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
if ($here -notmatch '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.+)\\scripts\\windows\\?$') {
    throw ("Run this from the project inside WSL " +
           "(\\wsl.localhost\<distro>\...\webapp\scripts\windows), " +
           "not from a copy on Windows. This copy is in: $here")
}
$distro = $Matches[1]
$appDir = '/' + ($Matches[2] -replace '\\', '/')

# Keep the launcher and icon on Windows, so the desktop icon shows even when
# WSL is not running yet.
$dest = Join-Path $env:LOCALAPPDATA 'SMCP'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $here 'smcp.ico') (Join-Path $dest 'smcp.ico') -Force

$launcher = Join-Path $dest 'start-smcp.bat'
$text = Get-Content -Raw (Join-Path $here 'start-smcp.bat.template')
$text = $text.Replace('@DISTRO@', $distro).Replace('@APP_DIR@', $appDir)
$text = $text -replace "`r?`n", "`r`n"          # cmd wants Windows line ends
[IO.File]::WriteAllText($launcher, $text, [Text.Encoding]::ASCII)

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = Join-Path $desktop 'SMCP Kallakudi.lnk'
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcut)
$lnk.TargetPath = $launcher
$lnk.WorkingDirectory = $dest
$lnk.IconLocation = (Join-Path $dest 'smcp.ico') + ',0'
$lnk.Description = 'Start the SMCP Kallakudi register and open it in the browser'
$lnk.Save()

Write-Host ""
Write-Host "Desktop icon created: $shortcut"
Write-Host "It starts $appDir in WSL ($distro) and opens http://localhost:8000/"
