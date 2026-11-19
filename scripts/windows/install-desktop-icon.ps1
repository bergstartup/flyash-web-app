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
$fromPath = $Matches[1]
$appDir = '/' + ($Matches[2] -replace '\\', '/')

# The name in the \\wsl.localhost path is not always the name `wsl -d`
# accepts (WSL_E_DISTRO_NOT_FOUND), so take the name from WSL's own list:
# the one the path names if it is there, else the one holding the project.
# `wsl -l` writes UTF-16 unless WSL_UTF8 is set; strip the NULs either way.
$env:WSL_UTF8 = '1'
$distros = @(wsl.exe --list --quiet | ForEach-Object { ($_ -replace "`0", '').Trim() } |
             Where-Object { $_ })
if (-not $distros) { throw "WSL lists no installed distributions (wsl --list)." }

function Test-Project([string]$name) {
    # Windows PowerShell turns a native command's stderr into an error, which
    # 'Stop' would throw; here a failure is an answer, not an error.
    $ErrorActionPreference = 'Continue'
    $null = wsl.exe -d $name -e test -f "$appDir/scripts/run.sh" 2>&1
    return ($LASTEXITCODE -eq 0)
}

$distro = $distros | Where-Object { $_ -ieq $fromPath } | Select-Object -First 1
if (-not $distro) {
    $distro = $distros | Where-Object { Test-Project $_ } | Select-Object -First 1
}
if (-not $distro) {
    throw ("None of the WSL distributions (" + ($distros -join ', ') +
           ") has the project at $appDir.")
}
if (-not (Test-Project $distro)) {
    throw "WSL distribution '$distro' has no $appDir/scripts/run.sh."
}

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
Write-Host "It starts $appDir in WSL distribution '$distro' and opens http://localhost:8000/"
if ($distro -ne $fromPath) {
    Write-Host "(The folder path said '$fromPath'; WSL knows it as '$distro'.)"
}
