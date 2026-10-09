<#
.SYNOPSIS
Registers the scheduled tasks for briefing-reader: "Briefing AM", "Briefing PM",
"Briefing catch-up" and "Briefing hotkey".

.DESCRIPTION
Creates (or replaces) these Windows Task Scheduler tasks for your user, all
started from this folder:

  Briefing AM        daily at -AmTime: ... -m briefing_reader --run am --slots ...
  Briefing PM        daily at -PmTime: ... -m briefing_reader --run pm --slots ...
  Briefing catch-up  at logon and when you unlock the PC (20 s later):
                     ... -m briefing_reader --catch-up --slots ...
  Briefing hotkey    at logon, and started right away:
                     ... -m briefing_reader --hotkey-agent --slots ...

The tasks start pythonw.exe of Python <PythonVersion> directly (found with
"py -<version>" when this script runs), not the pyw.exe App Execution Alias:
after sleep the alias can take minutes to start Python. Rerun this script
after installing or moving Python (or the folder) to find it again.

The AM and PM tasks wake the computer if it is asleep and run on battery.
Each waits for its briefing, then shows Jarvis without taking the focus and
announces it ("Sir, your AM briefing is ready to view."); nothing is read
until you press Play. The app hands its window to a separate process, so each
task ends within seconds (it never keeps the PC awake and has no time limit to
hit), and the app's own lock makes sure there is never a second copy: a start
while the window is open hands its run to that window.

The catch-up task announces a scheduled briefing of the last 3 hours that was
not announced or viewed (for example while the PC was asleep or locked); when
nothing is due, or the app is already open, it ends at once without showing
anything. The hotkey task listens for the global hotkey ([hotkey] in
config.toml, Ctrl+Alt+J by default) that opens Jarvis, until you log off.

The script is safe to rerun: it overwrites the tasks. That is also how the
app moves to another PC: copy the folder, install the packages, create .env,
run this script there.

Nothing else is changed. Power settings are only read: if wake timers are
off, the script prints the commands that turn them on.

.PARAMETER AmTime
Time of the morning task, 24-hour HH:mm. Default: [schedule] am in
config.toml (10:12).

.PARAMETER PmTime
Time of the evening task, 24-hour HH:mm. Default: [schedule] pm in
config.toml (23:42).

.PARAMETER PythonVersion
Python version passed to the py launcher (py -<version>). Default 3.13.

.PARAMETER Console
Run the AM, PM and catch-up tasks with python.exe instead of pythonw.exe, so
a console window shows the log output. For troubleshooting only: in this mode
the window stays inside the task, so the task keeps running (and keeps a PC it
woke awake) until the window is closed. The hotkey task stays windowless.

.PARAMETER UseLauncherAlias
Start the tasks through the py launcher's pyw.exe (py.exe with -Console) App
Execution Alias, as versions before 1.2 did, instead of pythonw.exe directly.

.PARAMETER NoHotkey
Do not register the "Briefing hotkey" task (and remove it if it exists). The
same happens when [hotkey] enabled = false in config.toml.

.PARAMETER DryRun
Check everything and print the tasks that would be registered, but register,
start, stop or remove nothing. Each task definition is built (not registered),
so a problem with it shows up here.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-schedule.ps1

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-schedule.ps1 -DryRun

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-schedule.ps1 -AmTime 07:30 -PmTime 18:05
#>
[CmdletBinding()]
param(
    [string]$AmTime = "",
    [string]$PmTime = "",
    [string]$PythonVersion = "3.13",
    [switch]$Console,
    [switch]$UseLauncherAlias,
    [switch]$NoHotkey,
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$TaskPath = "\"
$CatchUpTaskName = "Briefing catch-up"
$HotkeyTaskName = "Briefing hotkey"
$CatchUpDelay = "PT20S"
$TaskSessionUnlock = 8
$ImportCheck = "import briefing_reader, PySide6.QtMultimedia, edge_tts, pyttsx3, requests, dotenv"
# Reads [schedule] and [hotkey] from config.toml. environ={} keeps .env and the environment out of it.
$SettingsCheck = "import json; from briefing_reader.config import load_config; c = load_config(environ={}); " +
    "print(json.dumps({'am': c.schedule.am, 'pm': c.schedule.pm, 'hotkey': c.hotkey.enabled, 'combo': c.hotkey.combo}))"

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

function Write-Ok([string]$Text) {
    Write-Host $Text -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host ("WARNING: " + $Text) -ForegroundColor Yellow
}

function Stop-WithError([string]$Text) {
    Write-Host ("ERROR: " + $Text) -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

function ConvertTo-TimeOfDay([string]$Value, [string]$ParameterName) {
    $text = ""
    if ($null -ne $Value) { $text = $Value.Trim() }
    $m = [regex]::Match($text, '^([01]?[0-9]|2[0-3]):([0-5][0-9])$')
    if (-not $m.Success) {
        Stop-WithError ("-{0} '{1}' is not a valid time. Use 24-hour HH:mm, for example 10:12 or 23:42." -f $ParameterName, $Value)
    }
    return New-TimeSpan -Hours ([int]$m.Groups[1].Value) -Minutes ([int]$m.Groups[2].Value)
}

function Format-TimeOfDay([TimeSpan]$TimeOfDay) {
    return "{0:D2}:{1:D2}" -f $TimeOfDay.Hours, $TimeOfDay.Minutes
}

function Resolve-Launcher([string]$Name) {
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        $message = "$Name was not found. Install the Python Install Manager " +
            "(https://www.python.org/downloads/windows/ or the Microsoft Store), run 'py install $PythonVersion', " +
            "make sure the py.exe and pyw.exe aliases are on (Settings > Apps > Advanced app settings > " +
            "App execution aliases), then open a new PowerShell window and run this script again."
        Stop-WithError $message
    }
    return $command.Path
}

function Test-PythonSetup([string]$PyExe) {
    # A local "Continue" so text on stderr from py.exe is shown, never turned into a terminating error.
    $ErrorActionPreference = "Continue"
    Push-Location -LiteralPath $ProjectRoot
    try {
        $version = & $PyExe "-$PythonVersion" -c "import sys; print(sys.version.split()[0])"
        if ($LASTEXITCODE -ne 0 -or -not $version) {
            Stop-WithError ("Python $PythonVersion is not available through the py launcher. " +
                "Install it with: py install $PythonVersion   (Python Install Manager), then run this script again.")
        }
        Write-Host ("  Python {0} found." -f (($version | Select-Object -Last 1).ToString().Trim()))
        & $PyExe "-$PythonVersion" -c $ImportCheck
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "ERROR: briefing-reader or one of its packages cannot be imported (see the message above)." -ForegroundColor Red
            Write-Host "Install the packages from the project folder with:" -ForegroundColor Red
            Write-Host ("  py -{0} -m pip install -r requirements.txt" -f $PythonVersion)
            exit 1
        }
        Write-Ok "  Packages OK (briefing_reader, PySide6.QtMultimedia, edge_tts, pyttsx3, requests, dotenv)."
    }
    finally {
        Pop-Location
    }
}

function Resolve-Interpreter([string]$PyExe, [bool]$Windowless) {
    # The real python.exe / pythonw.exe behind "py -<version>", so the tasks do not go through
    # the App Execution Alias (pyw.exe), which can stall for minutes after the PC wakes up.
    $ErrorActionPreference = "Continue"
    $output = & $PyExe "-$PythonVersion" -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        Stop-WithError ("Could not ask Python $PythonVersion where it is installed. Rerun with -UseLauncherAlias " +
            "to start the tasks through pyw.exe instead.")
    }
    $python = ($output | Select-Object -Last 1).ToString().Trim()
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Stop-WithError ("Python $PythonVersion reports '$python', which does not exist. Rerun with " +
            "-UseLauncherAlias to start the tasks through pyw.exe instead.")
    }
    if (-not $Windowless) {
        return $python
    }
    $pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        Stop-WithError ("pythonw.exe was not found next to $python. Repair or reinstall Python $PythonVersion, " +
            "or rerun this script with -UseLauncherAlias to start the tasks through pyw.exe instead.")
    }
    return $pythonw
}

function Get-AppSettings([string]$PyExe) {
    # [schedule] and [hotkey] from config.toml, or $null (then the built-in defaults apply).
    $ErrorActionPreference = "Continue"
    Push-Location -LiteralPath $ProjectRoot
    try {
        $output = & $PyExe "-$PythonVersion" -c $SettingsCheck
        if ($LASTEXITCODE -ne 0 -or -not $output) { return $null }
        return (($output | Select-Object -Last 1).ToString() | ConvertFrom-Json)
    }
    catch {
        return $null
    }
    finally {
        Pop-Location
    }
}

function Test-UserVariable([string]$Name) {
    # True when the variable has a value in the user environment. The value is never kept or printed.
    $value = [Environment]::GetEnvironmentVariable($Name, "User")
    return ($null -ne $value -and $value.Trim().Length -gt 0)
}

function Get-EnvValueState([object[]]$Lines, [string]$Name) {
    # "value", "empty" or "missing" for $Name in the lines of .env. The value itself is never returned.
    $state = "missing"
    $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*=(.*)$'
    foreach ($line in $Lines) {
        $m = [regex]::Match([string]$line, $pattern)
        if (-not $m.Success) { continue }
        $value = $m.Groups[1].Value.Trim()
        if ($value.StartsWith("#")) { $value = "" }
        $value = $value.Trim([char[]]@('"', "'", ' ', "`t"))
        # The last line wins, as in python-dotenv.
        if ($value.Length -gt 0) { $state = "value" } else { $state = "empty" }
        $value = $null
    }
    return $state
}

function Write-EnvValueReport([string]$Name, [string]$State, [bool]$InUserEnvironment, [string]$What, [string]$Placeholder) {
    if ($State -eq "value") {
        Write-Ok "  .env has a $Name value (not shown)."
    }
    elseif ($InUserEnvironment) {
        Write-Ok "  .env has no $Name value, but it is set in your user environment (not shown)."
    }
    elseif ($State -eq "empty") {
        Write-Warn ".env has an empty $Name line. Paste $What after $Name= (README, setup step 4)."
    }
    else {
        Write-Warn ".env has no $Name line. Add one: $Name=<$Placeholder> (README, setup step 4)."
    }
}

function Test-EnvFile {
    # Only reports whether NOTION_TOKEN and BRIEFING_PAGE_ID have values. The values are never printed.
    $envPath = Join-Path $ProjectRoot ".env"
    $hasUserToken = Test-UserVariable "NOTION_TOKEN"
    $hasUserPage = Test-UserVariable "BRIEFING_PAGE_ID"

    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        if ($hasUserToken -and $hasUserPage) {
            Write-Ok "  No .env file, but NOTION_TOKEN and BRIEFING_PAGE_ID are set in your user environment (values not shown)."
            return
        }
        Write-Warn (".env not found in the project folder. Copy .env.example to .env, paste your Notion " +
            "integration secret after NOTION_TOKEN= and your briefing page's id after BRIEFING_PAGE_ID= " +
            "(README, setup step 4). The tasks are registered anyway; until then the app shows " +
            "'Notion token missing' or 'Notion page ID missing'.")
        return
    }

    try {
        $lines = @(Get-Content -LiteralPath $envPath -ErrorAction Stop)
    }
    catch {
        Write-Warn "Could not read .env, so NOTION_TOKEN and BRIEFING_PAGE_ID were not checked."
        return
    }

    $tokenState = Get-EnvValueState $lines "NOTION_TOKEN"
    $pageState = Get-EnvValueState $lines "BRIEFING_PAGE_ID"
    $lines = $null

    Write-EnvValueReport "NOTION_TOKEN" $tokenState $hasUserToken "your Notion integration secret" "your integration secret"
    Write-EnvValueReport "BRIEFING_PAGE_ID" $pageState $hasUserPage "the id (or the link) of your briefing page" "your page id"
}

function Get-WakeTimerState {
    # Returns @{ Plan; AC; DC } from "powercfg /query", or $null when the output
    # is not what we expect (localized Windows, missing setting, any error).
    $ErrorActionPreference = "Continue"
    try {
        $output = & powercfg.exe /query SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>$null
        if ($LASTEXITCODE -ne 0 -or $null -eq $output) { return $null }
        $text = ($output | Out-String)
        $ac = [regex]::Match($text, 'Current AC Power Setting Index:\s*0x([0-9A-Fa-f]+)')
        $dc = [regex]::Match($text, 'Current DC Power Setting Index:\s*0x([0-9A-Fa-f]+)')
        if (-not ($ac.Success -and $dc.Success)) { return $null }
        $plan = "current power plan"
        $planMatch = [regex]::Match($text, 'Power Scheme GUID:\s*[0-9A-Fa-f-]+\s+\((.+?)\)')
        if ($planMatch.Success) { $plan = 'power plan "' + $planMatch.Groups[1].Value + '"' }
        return @{
            Plan = $plan
            AC   = [Convert]::ToInt32($ac.Groups[1].Value, 16)
            DC   = [Convert]::ToInt32($dc.Groups[1].Value, 16)
        }
    }
    catch {
        return $null
    }
}

function Get-WakeTimerName([int]$Value) {
    switch ($Value) {
        0 { return "Disabled" }
        1 { return "Enabled" }
        2 { return "Important wake timers only" }
        default { return "unknown ($Value)" }
    }
}

function Show-WakeTimerCheck {
    Write-Host ""
    Write-Host "Wake timers"
    $state = Get-WakeTimerState
    if ($null -eq $state) {
        Write-Host "  Skipped: could not read 'Allow wake timers' (unexpected or localized powercfg output)."
        Write-Host "  Check it yourself: Control Panel > Power Options > Change plan settings >"
        Write-Host "  Change advanced power settings > Sleep > Allow wake timers: Enable, on battery and plugged in."
        return
    }
    $acName = Get-WakeTimerName $state.AC
    $dcName = Get-WakeTimerName $state.DC
    if ($state.AC -eq 1 -and $state.DC -eq 1) {
        Write-Ok ("  OK: 'Allow wake timers' is Enabled plugged in and on battery ({0})." -f $state.Plan)
        return
    }
    Write-Warn ("'Allow wake timers' in the {0}: plugged in = {1}, on battery = {2}." -f $state.Plan, $acName, $dcName)
    Write-Host "  The tasks can wake a sleeping PC only when this is Enabled ('Important wake timers only' is"
    Write-Host "  meant for system timers). To enable it for both, run (an administrator PowerShell may be needed):"
    Write-Host "    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1"
    Write-Host "    powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1"
    Write-Host "    powercfg /setactive SCHEME_CURRENT"
    Write-Host "  This script does not change power settings itself. The setting belongs to the current power plan."
}

# --------------------------------------------------------------------------
# Task definition
# --------------------------------------------------------------------------

function New-DailyTrigger([TimeSpan]$TimeOfDay) {
    $at = [datetime]::Today.Add($TimeOfDay)
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    # New-ScheduledTaskTrigger stores the start as UTC ("...Z"), which Task
    # Scheduler treats as "Synchronize across time zones": the local run time
    # would then move by an hour when daylight saving time starts or ends.
    # A local time without an offset stays at the same clock time all year.
    $trigger.StartBoundary = $at.ToString("yyyy-MM-dd'T'HH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture)
    return $trigger
}

function New-LogonTrigger([string]$UserId, [string]$Delay) {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
    if ($Delay) { $trigger.Delay = $Delay }
    return $trigger
}

function New-UnlockTrigger([string]$UserId, [string]$Delay) {
    # There is no New-ScheduledTaskTrigger switch for "On workstation unlock": build the CIM object.
    $class = Get-CimClass -Namespace "Root/Microsoft/Windows/TaskScheduler" -ClassName "MSFT_TaskSessionStateChangeTrigger"
    $trigger = New-CimInstance -CimClass $class -ClientOnly
    $trigger.StateChange = $TaskSessionUnlock
    $trigger.UserId = $UserId
    $trigger.Delay = $Delay
    $trigger.Enabled = $true
    return $trigger
}

function Get-NextOccurrence([TimeSpan]$TimeOfDay) {
    $candidate = [datetime]::Today.Add($TimeOfDay)
    if ($candidate -le (Get-Date)) { $candidate = $candidate.AddDays(1) }
    return $candidate
}

function Format-Program([string]$Path) {
    if ($Path.Contains(" ")) { return '"' + $Path + '"' }
    return $Path
}

function New-PlanAction([string]$Program, [string]$Arguments) {
    # A program path with a space is quoted, as Task Scheduler expects.
    return New-ScheduledTaskAction -Execute (Format-Program $Program) -Argument $Arguments -WorkingDirectory $ProjectRoot
}

function Test-Plan($Plan) {
    # Builds the complete task definition that Register-Plan would register, without registering it.
    New-ScheduledTask -Action $Plan.Action -Trigger $Plan.Triggers -Settings $Plan.Settings -Principal $principal `
        -Description $Plan.Description | Out-Null
}

function Get-ExistingTask([string]$Name) {
    return Get-ScheduledTask -TaskName $Name -TaskPath $TaskPath -ErrorAction SilentlyContinue
}

function Stop-IfRunning([string]$Name) {
    $task = Get-ExistingTask $Name
    if ($null -ne $task -and [string]$task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $Name -TaskPath $TaskPath
        Write-Host ("  Stopped the running '{0}'." -f $Name)
    }
}

function Register-Plan($Plan) {
    Register-ScheduledTask -TaskName $Plan.Name -TaskPath $TaskPath -Action $Plan.Action -Trigger $Plan.Triggers `
        -Settings $Plan.Settings -Principal $principal -Description $Plan.Description -Force | Out-Null
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

if ($PythonVersion -notmatch '^[0-9]+(\.[0-9]+)?(-[A-Za-z0-9]+)?$') {
    Stop-WithError ("-PythonVersion '{0}' is not a version like 3.13." -f $PythonVersion)
}

Write-Host "briefing-reader: scheduled tasks"
Write-Host ("  Project folder: {0}" -f $ProjectRoot)
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "briefing_reader\__main__.py") -PathType Leaf)) {
    Stop-WithError ("briefing_reader\__main__.py was not found in {0}. Keep this script in the briefing-reader project folder (next to README.md) and run it from there." -f $ProjectRoot)
}

$pyExe = Resolve-Launcher "py.exe"

Write-Host ""
Write-Host ("Checking Python {0} and packages..." -f $PythonVersion)
Test-PythonSetup $pyExe

# Program and arguments of the tasks. The hotkey agent never gets a console window.
if ($UseLauncherAlias) {
    $argPrefix = "-$PythonVersion "
    $agentProgram = Resolve-Launcher "pyw.exe"
    if ($Console) {
        $taskProgram = $pyExe
        $programNote = "py.exe App Execution Alias with a console window (-UseLauncherAlias -Console)"
    }
    else {
        $taskProgram = $agentProgram
        $programNote = "pyw.exe App Execution Alias, no console window (-UseLauncherAlias)"
    }
}
else {
    $argPrefix = ""
    $agentProgram = Resolve-Interpreter $pyExe $true
    if ($Console) {
        $taskProgram = Resolve-Interpreter $pyExe $false
        $programNote = "python.exe with a console window, because of -Console"
    }
    else {
        $taskProgram = $agentProgram
        $programNote = "pythonw.exe, no console window"
    }
    if ($taskProgram -match '\\Microsoft\\WindowsApps\\') {
        Write-Warn ("$taskProgram is an App Execution Alias (Microsoft Store Python). It works, but it may start " +
            "slowly after the PC wakes up. The Python Install Manager or python.org installer avoids that.")
    }
}
Write-Host ("  Program: {0} ({1})" -f $taskProgram, $programNote)

Write-Host ""
Write-Host "Reading [schedule] and [hotkey] from config.toml..."
$appSettings = Get-AppSettings $pyExe
$configAm = "10:12"
$configPm = "23:42"
$hotkeyEnabled = $true
$hotkeyCombo = "ctrl+alt+j"
if ($null -eq $appSettings) {
    Write-Warn "Could not read config.toml through Python; using the defaults (10:12, 23:42, hotkey ctrl+alt+j on)."
}
else {
    $configAm = [string]$appSettings.am
    $configPm = [string]$appSettings.pm
    $hotkeyEnabled = [bool]$appSettings.hotkey
    $hotkeyCombo = [string]$appSettings.combo
    Write-Host ("  [schedule] am = {0}, pm = {1}; [hotkey] enabled = {2}, combo = {3}" -f $configAm, $configPm, $hotkeyEnabled.ToString().ToLower(), $hotkeyCombo)
}
if ($PSBoundParameters.ContainsKey("AmTime")) { $amSource = "-AmTime" } else { $AmTime = $configAm; $amSource = "config.toml" }
if ($PSBoundParameters.ContainsKey("PmTime")) { $pmSource = "-PmTime" } else { $PmTime = $configPm; $pmSource = "config.toml" }
$amTimeOfDay = ConvertTo-TimeOfDay $AmTime "AmTime"
$pmTimeOfDay = ConvertTo-TimeOfDay $PmTime "PmTime"
if ($amSource -ne "config.toml" -or $pmSource -ne "config.toml") {
    if ((Format-TimeOfDay $amTimeOfDay) -ne $configAm -or (Format-TimeOfDay $pmTimeOfDay) -ne $configPm) {
        Write-Host ("  Note: the times differ from [schedule] in config.toml. The tasks pass their own times, but a manual")
        Write-Host ("  start uses config.toml; set am = ""{0}"" and pm = ""{1}"" there to match." -f (Format-TimeOfDay $amTimeOfDay), (Format-TimeOfDay $pmTimeOfDay))
    }
}
$installHotkey = $hotkeyEnabled -and -not $NoHotkey
$hotkeyReason = ""
if ($NoHotkey) { $hotkeyReason = "-NoHotkey" } elseif (-not $hotkeyEnabled) { $hotkeyReason = "[hotkey] enabled = false" }

Write-Host ""
Write-Host "Checking .env (Notion token and page id)..."
Test-EnvFile

$slotsArgument = "--slots am={0},pm={1}" -f (Format-TimeOfDay $amTimeOfDay), (Format-TimeOfDay $pmTimeOfDay)
$userId = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

# AM / PM: no time limit (zero) and Parallel: the app ends the task itself within seconds by
# handing its window to a detached process, and its own lock keeps a single window. A task-level
# limit would stop an open window, and IgnoreNew would silently skip a start whose run the
# open window should be told about.
$dailySettings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances Parallel -ExecutionTimeLimit ([TimeSpan]::Zero)
# Catch-up: no wake (it follows a logon or an unlock), one at a time, and 5 minutes at most:
# it exits within a second when nothing is due, or hands its window off like AM / PM.
# With -Console the window stays inside the task, so there is no limit then.
$catchUpLimit = New-TimeSpan -Minutes 5
if ($Console) { $catchUpLimit = [TimeSpan]::Zero }
$catchUpSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit $catchUpLimit
# Hotkey: runs until logoff (no limit); a second start while it runs is ignored.
$hotkeySettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

$descriptionTail = "Created by install-schedule.ps1; remove with uninstall-schedule.ps1."
$catchUpWhen = "at logon and on unlock of $userId, 20 s later; no wake, one at a time"
if ($Console) { $catchUpWhen += ", no time limit (-Console)" } else { $catchUpWhen += ", at most 5 min" }
$plans = @()
foreach ($daily in @(@{ Name = "Briefing AM"; Run = "am"; Label = "AM"; Time = $amTimeOfDay },
                     @{ Name = "Briefing PM"; Run = "pm"; Label = "PM"; Time = $pmTimeOfDay })) {
    $arguments = "${argPrefix}-m briefing_reader --run $($daily.Run) $slotsArgument"
    $plans += @{
        Name = $daily.Name; Program = $taskProgram; Arguments = $arguments; Settings = $dailySettings
        Action = (New-PlanAction $taskProgram $arguments)
        Triggers = @(New-DailyTrigger $daily.Time); Time = $daily.Time
        When = ("daily at {0}, wakes the PC" -f (Format-TimeOfDay $daily.Time))
        Description = ("briefing-reader: announces the $($daily.Label) email briefing from Notion when it is ready " +
            "(nothing is read until you press Play). " +
            "Runs '$(Split-Path -Leaf $taskProgram) $arguments' in $ProjectRoot. $descriptionTail")
    }
}
$catchUpArguments = "${argPrefix}-m briefing_reader --catch-up $slotsArgument"
$plans += @{
    Name = $CatchUpTaskName; Program = $taskProgram; Arguments = $catchUpArguments; Settings = $catchUpSettings
    Action = (New-PlanAction $taskProgram $catchUpArguments)
    Triggers = @((New-LogonTrigger $userId $CatchUpDelay), (New-UnlockTrigger $userId $CatchUpDelay)); Time = $null
    When = $catchUpWhen
    Description = ("briefing-reader: after logon or unlock, announces a scheduled briefing of the last 3 hours " +
        "that was not announced or viewed (nothing happens otherwise). " +
        "Runs '$(Split-Path -Leaf $taskProgram) $catchUpArguments' in $ProjectRoot. $descriptionTail")
}
if ($installHotkey) {
    $hotkeyArguments = "${argPrefix}-m briefing_reader --hotkey-agent $slotsArgument"
    $plans += @{
        Name = $HotkeyTaskName; Program = $agentProgram; Arguments = $hotkeyArguments; Settings = $hotkeySettings
        Action = (New-PlanAction $agentProgram $hotkeyArguments)
        Triggers = @(New-LogonTrigger $userId ""); Time = $null
        When = "at logon of $userId and right after installing; runs until logoff, one at a time; hotkey $hotkeyCombo"
        Description = ("briefing-reader: listens for the global hotkey ($hotkeyCombo, [hotkey] in config.toml) " +
            "that opens Jarvis. Runs '$(Split-Path -Leaf $agentProgram) $hotkeyArguments' in $ProjectRoot. " +
            $descriptionTail)
    }
}

Write-Host ""
if ($DryRun) {
    Write-Host "Planned tasks (-DryRun: nothing will be registered, started, stopped or removed)"
}
else {
    Write-Host "Registering tasks"
}
foreach ($plan in $plans) {
    Write-Host ("  {0}: {1}" -f $plan.Name, $plan.When)
    Write-Host ("    Program:  {0}" -f (Format-Program $plan.Program))
    Write-Host ("    Args:     {0}" -f $plan.Arguments)
    Write-Host ("    Start in: {0}" -f $ProjectRoot)
}
if (-not $installHotkey) {
    Write-Host ("  {0}: not installed ({1}); an existing one is stopped and removed." -f $HotkeyTaskName, $hotkeyReason)
    if ($DryRun -and $null -eq (Get-ExistingTask $HotkeyTaskName)) {
        Write-Host ("    (There is no '{0}' task now.)" -f $HotkeyTaskName)
    }
}
Write-Host ("  Runs as {0}, only while you are logged on (interactive, not elevated)." -f $userId)
Write-Host "  The AM and PM tasks wake the computer and run on battery. The app hands its window to a separate"
Write-Host "  process, so those tasks end within seconds and never keep the PC awake (no time limit is needed)."
if ($Console) {
    Write-Host "  -Console: the window stays inside the task, so the task runs until the window is closed."
}
Write-Host "  Each run announces its briefing when it is ready; nothing is read until you press Play."
Write-Host "  A run missed while the PC was asleep, off or locked is announced by the catch-up task when you"
Write-Host "  log on or unlock within 3 hours of it; later than that it is skipped."

$failed = @()
if ($DryRun) {
    Write-Host ""
    foreach ($plan in $plans) {
        try {
            Test-Plan $plan
        }
        catch {
            Write-Host ("ERROR: the task '{0}' could not be built: {1}" -f $plan.Name, $_.Exception.Message) -ForegroundColor Red
            $failed += $plan.Name
        }
    }
    if ($failed.Count -eq 0) {
        Write-Ok ("  All {0} task definitions are valid (built, not registered)." -f $plans.Count)
    }
    foreach ($plan in $plans) {
        if ($null -ne $plan.Time) {
            Write-Host ("  {0} would next run at {1}" -f $plan.Name, (Get-NextOccurrence $plan.Time).ToString("ddd yyyy-MM-dd HH:mm"))
        }
    }
    if ($installHotkey) {
        Write-Host ("  {0} would be started right away (Start-ScheduledTask)." -f $HotkeyTaskName)
    }
    elseif ($null -ne (Get-ExistingTask $HotkeyTaskName)) {
        Write-Host ("  {0} exists and would be stopped and removed." -f $HotkeyTaskName)
    }
    Write-Ok "  -DryRun: nothing was registered, started, stopped or removed."
}
else {
    foreach ($plan in $plans) {
        try {
            # A running hotkey agent keeps its old settings: stop it, so the start below uses the new ones.
            if ($plan.Name -eq $HotkeyTaskName) { Stop-IfRunning $plan.Name }
            Register-Plan $plan
        }
        catch {
            if ($plan.Name -like "Briefing ?M") {
                Stop-WithError ("Could not register '{0}': {1}" -f $plan.Name, $_.Exception.Message)
            }
            Write-Host ("ERROR: could not register '{0}': {1}" -f $plan.Name, $_.Exception.Message) -ForegroundColor Red
            $failed += $plan.Name
        }
    }
    if (-not $installHotkey -and $null -ne (Get-ExistingTask $HotkeyTaskName)) {
        try {
            Stop-IfRunning $HotkeyTaskName
            Unregister-ScheduledTask -TaskName $HotkeyTaskName -TaskPath $TaskPath -Confirm:$false
            Write-Host ("  Removed '{0}' ({1})." -f $HotkeyTaskName, $hotkeyReason)
        }
        catch {
            Write-Host ("ERROR: could not remove '{0}': {1}" -f $HotkeyTaskName, $_.Exception.Message) -ForegroundColor Red
            $failed += $HotkeyTaskName
        }
    }
    if ($installHotkey -and $failed -notcontains $HotkeyTaskName) {
        try {
            Start-ScheduledTask -TaskName $HotkeyTaskName -TaskPath $TaskPath
            Write-Host ("  Started '{0}': {1} works now, without logging off." -f $HotkeyTaskName, $hotkeyCombo)
        }
        catch {
            Write-Warn ("Could not start '{0}' now ({1}); it starts at your next logon." -f $HotkeyTaskName, $_.Exception.Message)
        }
    }
    Write-Host ""
    foreach ($plan in $plans) {
        if ($failed -contains $plan.Name) { continue }
        $info = Get-ScheduledTaskInfo -TaskName $plan.Name -TaskPath $TaskPath
        $next = "at logon / unlock"
        if ($null -ne $plan.Time) {
            $next = "not scheduled"
            if ($null -ne $info.NextRunTime) { $next = ([datetime]$info.NextRunTime).ToString("ddd yyyy-MM-dd HH:mm") }
        }
        elseif ($plan.Name -eq $HotkeyTaskName) {
            $next = "at logon (running now)"
        }
        Write-Ok ("  Registered '{0}', next run: {1}" -f $plan.Name, $next)
    }
}

Show-WakeTimerCheck

Write-Host ""
Write-Host "Single instance"
Write-Host "  Only one briefing window runs at a time: the app holds its own lock, so an AM, PM or manual run"
Write-Host "  that starts while another one is open hands its run to the open window and exits at once."
Write-Host "  The catch-up task leaves an open window alone."
Write-Host ""
if ($DryRun -and $failed.Count -gt 0) {
    Write-Host ("-DryRun found a problem with {0} (see above)." -f ($failed -join ", ")) -ForegroundColor Red
    exit 1
}
elseif ($DryRun) {
    Write-Host "Run again without -DryRun to register the tasks."
}
elseif ($failed.Count -gt 0) {
    Write-Host ("Done, but {0} could not be set up (see above). The AM and PM tasks are registered." -f ($failed -join ", ")) -ForegroundColor Red
    exit 1
}
else {
    Write-Host "Done. Rerun this script to change the times; run uninstall-schedule.ps1 to remove the tasks."
}
exit 0
