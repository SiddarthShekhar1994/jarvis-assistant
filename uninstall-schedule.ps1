<#
.SYNOPSIS
Removes the Jarvis Assistant scheduled tasks created by install-schedule.ps1:
"Briefing AM", "Briefing PM", "Briefing catch-up" and "Briefing hotkey".

.DESCRIPTION
Unregisters the tasks that exist and reports what was removed and what was
not found. A running "Briefing hotkey" agent is stopped first, so the hotkey
is released at once. Nothing else is changed: the project folder, .env, logs
and power settings are left alone. A Jarvis window that is open right now
keeps running until you close it.

Use -WhatIf to see what would be removed without removing anything.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall-schedule.ps1

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall-schedule.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$TaskPath = "\"
$TaskNames = @("Briefing AM", "Briefing PM", "Briefing catch-up", "Briefing hotkey")

$removed = @()
$notFound = @()
$failed = @()

foreach ($name in $TaskNames) {
    $task = Get-ScheduledTask -TaskName $name -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host ("Not found: {0} (nothing to remove)" -f $name)
        $notFound += $name
        continue
    }
    if (-not $PSCmdlet.ShouldProcess($name, "Stop (if running) and unregister scheduled task")) {
        continue
    }
    try {
        if ([string]$task.State -eq "Running") {
            # The hotkey agent runs until logoff; stopping it releases the hotkey now.
            Stop-ScheduledTask -TaskName $name -TaskPath $TaskPath
            Write-Host ("Stopped:   {0}" -f $name)
        }
        Unregister-ScheduledTask -TaskName $name -TaskPath $TaskPath -Confirm:$false
        Write-Host ("Removed:   {0}" -f $name) -ForegroundColor Green
        $removed += $name
    }
    catch {
        Write-Host ("ERROR: could not remove '{0}': {1}" -f $name, $_.Exception.Message) -ForegroundColor Red
        $failed += $name
    }
}

Write-Host ""
Write-Host ("Summary: {0} removed, {1} not found, {2} failed." -f $removed.Count, $notFound.Count, $failed.Count)
if ($failed.Count -gt 0) {
    Write-Host "Open Task Scheduler (taskschd.msc) to remove the remaining task(s) by hand." -ForegroundColor Red
    exit 1
}
exit 0
