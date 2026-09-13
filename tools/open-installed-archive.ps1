param([Parameter(Mandatory=$true)][string]$Executable)
$ErrorActionPreference = 'Stop'

# Run outside the Codex sandbox. Filesystem permission alone does not grant
# access to the user's interactive desktop or Windows-protected credentials.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$session = (Get-Process -Id $PID).SessionId
$explorers = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | Where-Object { $_.SessionId -eq $session })
$owners = @($explorers | ForEach-Object {
    $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
    if ($owner.ReturnValue -ne 0) { throw 'Cannot verify desktop owner.' }
    $owner.Domain + '\' + $owner.User
})
if ($owners -notcontains $identity) { throw "Refusing GUI launch as ${identity}: this account does not own Explorer in session $session." }
$exe = (Resolve-Path -LiteralPath $Executable).Path
$existing = @(Get-CimInstance Win32_Process -Filter "Name='ChatterinoArchive-by-Mist1X.exe'" | Where-Object { $_.ExecutablePath -eq $exe })
foreach ($process in $existing) {
    $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner
    if ($owner.ReturnValue -ne 0 -or ($owner.Domain + '\' + $owner.User) -ne $identity -or $process.SessionId -ne $session) {
        throw 'An archive process belongs to another desktop/account. Stop it gracefully before launching.'
    }
}

# This is the interactive app the user asked to open, not a background helper.
Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ArchiveWindowCheck {
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
}
'@
$deadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 300
    $windows = @(Get-Process -Name 'ChatterinoArchive-by-Mist1X' -ErrorAction SilentlyContinue | Where-Object {
        $_.SessionId -eq $session -and $_.MainWindowTitle -like '*Chatterino*' -and
        $_.MainWindowHandle -ne 0 -and [ArchiveWindowCheck]::IsWindowVisible($_.MainWindowHandle)
    })
} while ($windows.Count -eq 0 -and (Get-Date) -lt $deadline)
if ($windows.Count -ne 1) { throw 'Could not verify one visible archive window on the Explorer desktop.' }
[pscustomobject]@{ DesktopOwner=$identity; Session=$session; ProcessId=$windows[0].Id; Title=$windows[0].MainWindowTitle; Visible=$true }
