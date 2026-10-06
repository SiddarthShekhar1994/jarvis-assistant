# briefing-reader

A small Windows desktop app that reads your email briefing aloud. Twice a day a
scheduled AI task (for example a Claude scheduled task) overwrites a Notion page,
such as "Daily Briefing (auto)", with an email triage. briefing-reader starts on
a schedule (10:12 and 23:42 by default; both configurable), pops up a small
always-on-top window that asks "Your AM briefing is ready. Hear it now?", fetches
the page in the background while it waits, and reads it aloud when you click
**Read now**. It never starts reading on its own. While it reads, the text is
shown with the current line highlighted.

The briefing can also propose calendar events. They appear in an amber
**NEEDS YOUR OK** column, and **Approve** adds one to your Google Calendar.
Nothing is ever added without your Approve (see [Calendar actions](#calendar-actions)).
Next to the reading, a **TODAY** panel lists the day's calendar events (from
18:00 on: tomorrow's) and **DEADLINES** lists what is due in the next 14 days
(see [TODAY and DEADLINES](#today-and-deadlines)).

If the PC was asleep or locked at the scheduled time, the app asks when you log
on or unlock within 3 hours, and a global hotkey (**Ctrl+Alt+J**) reads the
briefing at any time (see [Catch-up and the hotkey](#catch-up-and-the-hotkey)).

Both windows use the "Jarvis HUD" look: a dark, frameless sci-fi panel with a
glowing status orb (see [The look](#the-look-jarvis-hud)).

This is a personal Windows project, built with Claude Code and shared as is
under the MIT licence (see [License](#license)). It brings no Notion page of its
own: you point it at yours (setup steps 2 to 4), and the page is written by your
own scheduled task (see [Instructions for the Claude briefing task](#instructions-for-the-claude-briefing-task)).

## Requirements

- Windows 10 or 11.
- Python 3.13 through the `py` launcher (Python Install Manager or the python.org
  installer). All commands below use `py -3.13` because plain `py` may default to
  another Python version that does not have the packages. The scheduled tasks
  start that Python's `pythonw.exe` (the windowless interpreter) directly, so no
  console window flashes up.
- A Notion page that a scheduled task rewrites with your briefing (format:
  [Instructions for the Claude briefing task](#instructions-for-the-claude-briefing-task)),
  and a Notion integration that may read it (setup steps 2 and 3).
- Internet access for Notion and for the online voice (edge-tts). If the online
  voice cannot be reached, the app falls back to the Windows built-in voices.
- For calendar actions and the TODAY panel only: a Google account and a free
  Google Cloud project with your own OAuth client (setup step 8). Without it,
  everything else works: proposals are still shown (Approve just says that
  Google Calendar is not set up yet), and DEADLINES still lists the
  briefing's own deadlines.

## Setup

Run these in PowerShell from the project folder (the folder that contains this
README and `config.toml`; for example the folder you cloned or unzipped):

```powershell
cd "<project folder>"
```

1. **Install the dependencies**

   ```powershell
   py -3.13 -m pip install -r requirements.txt
   ```

   This includes the Google client libraries for calendar actions
   (google-api-python-client, google-auth-oauthlib, google-auth-httplib2). If you
   installed an earlier version of the app, run it again.

2. **Create the Notion integration**

   Open <https://www.notion.so/profile/integrations> and click **New integration**.
   Choose type **Internal**, pick your workspace, and name it `briefing-reader`.
   Under **Capabilities** keep only **Read content** (uncheck Update content and
   Insert content; no user information is needed). Save, then copy the
   **Internal Integration Secret** (it starts with `ntn_`, or `secret_` for older
   integrations).

3. **Share the page with the integration, and copy its id**

   Open your briefing page (for example "Daily Briefing (auto)") in Notion,
   click the **...** menu (top right) > **Connections** (called **Connect to** in
   some versions) > pick **briefing-reader** > **Confirm**. Newer Notion versions
   also let you pick the page on the integration's **Access** tab. Without this
   step the Notion API answers 404 (the page looks like it does not exist), and
   the app says that the page is not found or not shared with the integration.

   Then copy the page's link (**Share** > **Copy link**, or the address bar of
   the browser). The page id is the 32 letters and digits at the end of the link,
   after the page title:

   ```
   https://www.notion.so/your-workspace/Daily-Briefing-0123456789abcdef0123456789abcdef?pvs=4
                                                       ^ page id: 0123456789abcdef0123456789abcdef
   ```

   (That id is only an example.) You can paste the id, the dashed form
   (`01234567-89ab-cdef-0123-456789abcdef`) or the whole link in the next step.

4. **Create `.env`**

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   Fill in both lines and save:

   ```
   NOTION_TOKEN=<the Internal Integration Secret from step 2>
   BRIEFING_PAGE_ID=<the page id (or link) from step 3>
   ```

   Both are required: without `NOTION_TOKEN` the app shows "Notion token
   missing.", and without a valid `BRIEFING_PAGE_ID` it shows "Notion page ID
   missing." instead of reading anything (the scheduled tasks too; the log says
   why). `.env` must be in the project folder, next to `README.md` and
   `config.toml`. Never commit or share it (`.gitignore` already excludes it).

   If the project folder is inside OneDrive (or another synced folder), `.env` is
   synced to that account and to other PCs that sync it. The integration is
   read-only, so the secret can only read pages you shared with it. If that still
   matters to you, move the project out of the synced folder (for example to
   `C:\Tools\briefing-reader`) and rerun `install-schedule.ps1` there. If you keep
   it in OneDrive with Files On-Demand, right-click the folder > **Always keep on
   this device**, so a scheduled run never waits for a download.

5. **Try it**

   ```powershell
   py -3.13 -m briefing_reader --now      # skips the prompt, fetches and reads immediately
   py -3.13 -m briefing_reader --run am   # the prompt, plus waiting for today's AM briefing
   ```

6. **Schedule it**

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-schedule.ps1
   ```

   Options: `-DryRun` checks everything and prints the planned tasks without
   registering, starting, stopping or removing anything (it builds each task
   definition, unregistered, so a problem shows up before anything is
   registered); `-AmTime 07:30 -PmTime
   18:05` (24-hour `HH:mm`) changes the times (without them the script uses
   `[schedule]` in `config.toml`, 10:12 and 23:42 by default); `-Console` runs the
   AM, PM and catch-up tasks with `python.exe` instead of `pythonw.exe`, so a
   console window shows the log (for troubleshooting); `-NoHotkey` leaves out the
   hotkey task (and removes it if it exists); `-UseLauncherAlias` starts the tasks
   through `pyw.exe` as before version 1.2 (see below); `-PythonVersion` defaults
   to 3.13.

   The default times are only examples. Pick times a few minutes after your own
   scheduled task usually finishes writing the page (the app waits up to 15
   minutes for it anyway, see `--run` under [Command line](#command-line)).

   The script checks that Python 3.13 and the packages import, reads `[schedule]`
   and `[hotkey]` from `config.toml`, warns if `.env` has no `NOTION_TOKEN` or no
   `BRIEFING_PAGE_ID` value (it never prints the values), and registers four
   tasks for your user:

   | Task | When | Runs (in the project folder) |
   |---|---|---|
   | Briefing AM | daily 10:12 | `pythonw.exe -m briefing_reader --run am --slots am=10:12,pm=23:42` |
   | Briefing PM | daily 23:42 | `pythonw.exe -m briefing_reader --run pm --slots am=10:12,pm=23:42` |
   | Briefing catch-up | at logon and when you unlock the PC, 20 s later | `pythonw.exe -m briefing_reader --catch-up --slots am=10:12,pm=23:42` |
   | Briefing hotkey | at logon (and started right away) | `pythonw.exe -m briefing_reader --hotkey-agent --slots am=10:12,pm=23:42` |

   `pythonw.exe` is the full path of Python 3.13's windowless interpreter, which
   the script finds with `py -3.13` and prints. The tasks used to start the `pyw`
   App Execution Alias instead; right after a PC wakes from sleep, that alias can
   take minutes to start Python, so the prompt came late. Rerun the
   script after installing or moving Python and it finds the interpreter again.
   `-UseLauncherAlias` goes back to the `pyw.exe` alias if you ever need it.
   `--slots` tells the app the scheduled times, so it knows which briefing an
   answer belongs to (see [Catch-up and the hotkey](#catch-up-and-the-hotkey)).

   The AM and PM tasks wake the computer to run, start on battery, and run only
   while you are logged on (the window needs your desktop, so the script runs
   from a normal, non-administrator PowerShell). The app hands its window to a
   separate process and the task itself ends within seconds, so a task never
   keeps a PC it woke from going back to sleep, and there is no task time limit
   that could close a window you have not answered yet. The app keeps its own
   lock, so an AM, PM or manual run that starts while another one is open only
   passes its run to the open window (the tasks allow parallel starts for exactly
   that). With `-Console` the window stays inside the task instead, so that task
   keeps running (and keeps a PC it woke awake) until you close the window; use
   it for troubleshooting only. The start time is stored as local time, so it
   does not shift when daylight saving time starts or ends. The script prints the
   next run time of each task.

   The catch-up task never wakes the PC, runs one at a time and is stopped
   after 5 minutes at most (it normally ends within a second, or hands its window
   off like the AM and PM tasks; with `-Console` it has no limit). The hotkey task
   runs until you log off; the script starts it right away, so the hotkey works
   without logging off. If `[hotkey] enabled = false` or `-NoHotkey` is given, the
   hotkey task is not installed and an existing one is stopped and removed. The
   hotkey task always runs windowless, also with `-Console`.

   **Wake timers.** A task can wake a sleeping PC only when the power plan setting
   "Allow wake timers" is **Enable**, both plugged in and on battery. The script
   reads this setting and, if it is off, prints the exact `powercfg` commands to
   turn it on; it does not change power settings itself. Wake timers can wake a PC
   from sleep and usually from hibernate, but not from a full shutdown, and the
   result also depends on the hardware. If the PC is off or does not wake at the
   scheduled time, the catch-up task asks about that run when you log on or
   unlock within 3 hours of it; later than that it is skipped ("start the task
   as soon as possible after a missed start" is deliberately not set, so a
   laptop that was off does not pop up a stale briefing hours later). If the PC
   wakes while you are away, the prompt keeps coming back (it hides itself after
   2 minutes without an answer and asks again 10 minutes later), so it is there
   when you come back; since the task has already ended, Windows can put the PC
   back to sleep meanwhile.

7. **Moving to another PC**

   Copy the folder to the other PC (or clone it), install Python 3.13 and the
   packages (step 1), create `.env` (step 4; it is not in git), copy
   `google_client_secret.json` if you use calendar actions (step 8; it is not in
   git either), and run `install-schedule.ps1` there; it finds that PC's
   `pythonw.exe` again. The Google sign-in is kept per PC, so the first Approve
   on the new PC opens the Google sign-in once. Rerunning the script overwrites
   the tasks, which is also how you change the times (or set them in `[schedule]`
   in `config.toml` and rerun it). Run `uninstall-schedule.ps1` on the old PC if
   it should stop asking.

8. **Google Calendar setup (for calendar actions and TODAY)**

   Only needed if you want **Approve** to add proposed events to Google Calendar
   and the reading screen to show your day's events.
   You create your own small OAuth app in Google Cloud, so briefing-reader talks
   to Google only as you. It is free; no billing account is needed.

   1. Open <https://console.cloud.google.com/>, signed in with the Google account
      whose calendar should get the events. In the project picker at the top,
      click **New project**, name it `briefing-reader`, **Create**, and make sure
      it is selected.
   2. **APIs & Services > Library**: search for **Google Calendar API**, open it,
      click **Enable**.
   3. **APIs & Services > OAuth consent screen** (newer consoles call it **Google
      Auth Platform** and start with **Get started**): app name
      `briefing-reader`, user support email = your email, audience **External**,
      contact email = your email, accept the policy, **Create**.
   4. **Audience**: under **Test users** click **Add users** and add your own
      Google address. Then, on the same page, set the **Publishing status** to
      **In production** (**Publish app** > **Confirm**). An app left in
      **Testing** gets sign-ins that expire after 7 days, so you would have to
      sign in again every week. Verification by Google is not needed for an app
      only you use (the console may suggest it; you can ignore that). Because the
      app is not verified, the first sign-in shows "Google hasn't verified this
      app": click **Advanced** > **Go to briefing-reader (unsafe)**. It is safe
      here because the app is your own.
   5. **Clients** (older consoles: **Credentials > Create credentials > OAuth
      client ID**): **Create client**, application type **Desktop app**, name
      `briefing-reader`, **Create**. In the dialog that confirms it, click
      **Download JSON**. Newer consoles show the client secret only in that
      dialog; if you closed it, open the client and add a new secret, or create
      another client.
   6. Save the downloaded file (named like `client_secret_....apps.googleusercontent.com.json`)
      in the project folder as **`google_client_secret.json`**, next to
      `config.toml`, and then delete the downloaded original, so only one copy
      is left. `.gitignore` excludes it (and any file with `client_secret` in
      its name, such as the download itself or `google_client_secret.json.json`).
      Treat it as private, like `.env`; a synced folder syncs it the same way
      (see step 4). Another name or location works too: set `[calendar]
      client_secret` in `config.toml`, and keep the file outside the project
      folder or in a `secrets\` folder inside it, which `.gitignore` also
      excludes.
   7. That's all. The first time you click **Approve**, or **Connect** in the
      TODAY panel, your browser opens the
      Google sign-in: pick your account and allow both permissions. The page
      then says "briefing-reader is connected to Google Calendar. You can close
      this tab." The sign-in must be finished within 5 minutes; the app stays
      usable meanwhile. It is saved in
      `%LOCALAPPDATA%\briefing-reader\google_token.json` (on this PC only, outside
      the project folder) and refreshed automatically, so later approvals do not
      ask again.
      The app never opens the sign-in by itself: only those two clicks do.

   **Permissions the app asks for**

   | Scope | Why |
   |---|---|
   | `https://www.googleapis.com/auth/calendar.events` | Add the event you approved, and first look for the same event (same title and start) so it is not added twice; read your events of today (or tomorrow) and the next 14 days for TODAY and DEADLINES. The app only reads and inserts events; it never changes or deletes one. |
   | `https://www.googleapis.com/auth/calendar.settings.readonly` | Read your calendar's time zone, so that "15:00" means 15:00 where your calendar is. |

   No access to Gmail, Drive, contacts or anything else.

   **Disconnecting.** Delete `%LOCALAPPDATA%\briefing-reader\google_token.json`
   (the next Approve signs in again), and remove the app's access at
   <https://myaccount.google.com/permissions> (pick briefing-reader and remove
   its access). To stop using calendar actions without disconnecting, set
   `[calendar] enabled = false` in `config.toml`.

9. **Uninstall**

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall-schedule.ps1
   ```

   It removes "Briefing AM", "Briefing PM", "Briefing catch-up" and "Briefing
   hotkey" if they exist (the hotkey agent is stopped first, so the hotkey is
   free at once) and reports what was removed or not found (`-WhatIf` shows what
   it would do). Files, `.env`, logs, the Google sign-in and saved decisions are
   left alone; see "Disconnecting" in step 8 and
   [Logs and saved files](#logs-and-saved-files).

## The look (Jarvis HUD)

The windows follow the "Jarvis HUD - sci-fi" design: a near-black ground with a
faint cyan grid and glow, panels with cut (chamfered) corners, cyan accents,
amber for anything that needs your OK, and three fonts (see [Fonts](#fonts)):
Chakra Petch for the JARVIS wordmark, Sora for text and JetBrains Mono for
labels and times.

- **Frameless window.** The header bar is the title bar: drag it to move the
  window. The small **X** at its right end closes it (on the prompt that counts
  as Later (10 min), on the reading screen it is the same as Done). The window
  stays on top of other windows.
- **Header.** The JARVIS wordmark, three service chips and the date and time,
  such as TUE 06 OCT 1:05 PM (`[display] clock = "24h"` shows 13:05; the
  small prompt window has room for the time only):
  **notion** (grey: checking, green: loaded, amber: retrying, red: error),
  **voice** (grey: no audio yet, green: online voice, amber: offline Windows
  voice, red: no speech could be generated) and **calendar** (green:
  signed in to Google, amber: not signed in yet, grey: turned off or not set
  up). Hover over a chip for details; the calendar chip also says how many
  proposals need your OK.
- **The orb** shows what the app is doing, with the state written next to it:
  WAITING ON YOU (amber), WORKING (cyan, while the briefing loads or the reading
  waits for its audio), SPEAKING (pink, while reading), STANDBY (grey: paused,
  finished, or something went wrong). It is animated only while the window is
  visible and not minimized.

## Using it

**The prompt.** On launch the window appears at the bottom right, on top of other
windows, and takes focus once. The orb is on the left; next to it the question
("Your AM briefing is ready. Hear it now?"), when the page was last updated (for
example "UPDATED TODAY AT 10:04 AM"), whether the audio is ready and, if the page
proposes calendar events you have not decided yet, an amber chip such as
"2 NEED YOUR OK". It offers **Read now**, **Later (10 min)** and **Later (30 min)**,
with a small **Dismiss** link underneath. No button is the default, so keys you
were typing elsewhere at that moment cannot press one.

- **Later** hides the window and asks again after that delay. The prompt keeps
  coming back until you choose Read now or Dismiss.
- If you do not answer within 2 minutes, that counts as Later (10 min). A small
  countdown under the buttons shows when this happens.
- Prompts that come back after Later appear on top but do not take keyboard focus
  (`refocus_on_reprompt` in `config.toml` changes that).
- Closing the window with the X in its header counts as Later (10 min). (When
  the Notion token or page ID is missing, Later is not offered and the X quits
  like Dismiss.)
- **Dismiss** quits for this run.
- With `--run am|pm`, if the page has not been updated yet, the prompt says
  "Your AM briefing hasn't arrived yet." and checks every minute for up to 15
  minutes. **Read now** reads the last briefing anyway (with a spoken stale note).
  If no fresh page arrives in that time, the prompt asks "Your AM briefing may be
  stale. Hear it anyway?". It still looks again later: each time the prompt comes
  back, and when you click **Read now** (waiting at most 5 seconds for Notion),
  the page is checked once more, so a briefing that arrives late is picked up.
- If Notion cannot be reached, the prompt says "Couldn't load your briefing." with
  the reason; **Read now** tries again at once.
- While the window is hidden, the tray icon stays in the notification area (it may
  be under the ^ overflow arrow). Click it to bring the prompt back now; right-click
  for Show briefing and Dismiss (Quit while reading).

**The reading screen.** A larger window (about 1120 x 700, resizable down to
900 x 600; on a smaller screen it fits the screen) with three columns:

- **Left: STATUS, TODAY and DEADLINES.** STATUS shows Progress (sections
  played of all), Voice (online, or offline for the Windows voice), Updated,
  and Needs your OK (how many proposals wait for a decision). Below it, one
  panel lists the day's calendar events and then what is due soon (see
  [TODAY and DEADLINES](#today-and-deadlines)); it scrolls when it does not fit.
- **Middle: the reading.** The orb and its state, the sentence being spoken in
  large type, and the buttons. Below them is the text panel: chips for the
  current section and its neighbours, a blinking LIVE marker while it plays, a
  **SECTIONS** button, and the briefing text with the line being spoken marked
  by a glowing cyan bar. The panel scrolls along unless you just scrolled
  yourself. **SECTIONS** opens the list of the briefing's sections: played ones
  green, the current one cyan, upcoming ones dim, and the Ignore section grey
  with "not read". Click a section to jump there (the Ignore section after
  **Read everything**); a click outside the list or Esc closes it.
- **Right: NEEDS YOUR OK and ACTIVITY.** The calendar proposals (see
  [Calendar actions](#calendar-actions)) and a log of what the app did, newest
  first: fetched the briefing, prepared the audio, read a section, Google
  sign-in, event added.

Buttons:

- **Pause / Play** (Space does the same, unless you moved the keyboard focus to a
  button with Tab: then Space presses that button). It becomes **Replay** at the
  end, and **Retry** if the page could not be loaded (Retry fetches the page
  again right away).
- **Skip section** jumps to the next section.
- **Read everything** also reads the Ignore section (shown dimmed with "(not read
  aloud)" until then). It is disabled when the page has no Ignore section.
- **Open in Notion** opens the page's notion.so link.
- **Done** closes the app. Closing the window with the X in its header is the
  same as Done.

If the speech for a section could not be generated, that section is skipped: its
row in SECTIONS says "no audio", and the STATUS panel and the activity log say
so.

## TODAY and DEADLINES

The left column of the reading screen shows your day next to the briefing.

**TODAY** lists the events on your Google Calendar for today; from 18:00 on it
says **TOMORROW** and lists tomorrow's (`[agenda] evening_from_hour`). Each row
has a coloured bar (cyan: still to come, green: happening now, dim: over), the
start time ("9:00 AM", or "ALL DAY" for all-day events), the title and a short
line: "now", "in 20 min" (up to an hour ahead) or "ended", and the place. A
place that is only a meeting link is left out. Point at a row for its full
text. The calendars are `[agenda] calendars` (your main calendar by default).

The panel reads your calendar when the reading screen opens, every 10 minutes
while it is visible (never while it is hidden or minimized), after you sign in
and after an Approve added an event; its corner shows how many events there
are ("5 BLOCKS"). Reading the calendar never opens the Google sign-in, and it
runs on the same background worker as approvals, one call at a time. Privacy:
once Google Calendar is connected, simply opening the reading screen reads
today's (or tomorrow's) events and those of the next 14 days from Google (see
[Privacy and cost](#privacy-and-cost)). What TODAY says instead of rows:

| Line | Meaning |
|---|---|
| Google Calendar is not set up (README step 8) | No `google_client_secret.json` (or the Google packages are missing). |
| Google Calendar is turned off (config.toml) | `[calendar] enabled = false`. |
| Connect Google Calendar to see your day + **Connect** | Not signed in on this PC yet (or the sign-in was revoked). **Connect** opens the same Google sign-in as an Approve; nothing happens until you click it. If the sign-in fails, the reason is shown in amber with **Connect** again. |
| Waiting for Google sign-in... | The sign-in is open in your browser (from Connect or from an Approve). |
| Loading... | The first read is on its way. |
| Couldn't load the calendar (amber) | Google could not be reached or answered with an error; point at the line for the reason. It is tried again 10 minutes later. When an earlier read worked, its rows stay and the panel's corner says NOT UPDATED instead. |
| Nothing on the calendar | The day has no events. |

**DEADLINES** lists what is due in the next 14 days (`[agenda] deadline_days`),
soonest first, at most 8: the lines of the briefing's "Deadlines" section (see
[the format](#deadlines-format)) and calendar events whose title says they are
due (words in `[agenda] deadline_keywords`, such as "due", "exam" or "quiz";
their source is CALENDAR). The same deadline from both is listed once. Each row
shows the title, the source in capitals and, on the right, when it is due:
TODAY (or "TODAY 3:00 PM", "TONIGHT 11:59 PM"), TOMORROW, "2D" to "13D", or a
date such as "OCT 20"; red for today and tomorrow, amber for 2 to 3 days, grey
after that. Today's deadlines stay until the day ends. DEADLINES works without
Google Calendar (the briefing's own deadlines only) and says "No deadlines in
the next 14 days" when there are none.

## Catch-up and the hotkey

**Catch-up.** The app keeps track of each scheduled briefing (a "slot", such as
the PM briefing of October 4): when its prompt was first shown, and whether you
answered it. **Read now**, **Dismiss** and **Done** settle the current slot, and
so does starting to read with the hotkey or `--now`. An answer up to 18 hours
after a slot belongs to that slot, so answering the PM prompt after midnight
still settles the evening's briefing; **Later** settles nothing (the app is
still open then). When you log on or unlock the PC, the "Briefing catch-up" task
checks 20 seconds later:

- if the app is already open, nothing happens (it does not take the focus at
  every unlock);
- if a slot passed in the last 3 hours and was not answered, the prompt appears
  exactly as if the AM or PM task had just run (including the wait for a fresh
  briefing; after midnight, a PM catch-up takes the PM briefing of the evening
  before as fresh, as the 23:42 task would have);
- otherwise nothing happens.

Only the latest slot counts: once the PM time has passed, a missed AM briefing
is not asked about any more. When nothing is due the check takes well under a
second and loads no window code; it always writes one line with its decision
to the log ("Catch-up: ..."). The slot times come from `--slots` in the tasks,
or from `[schedule]` in `config.toml` when you start the app by hand. The record
is `%LOCALAPPDATA%\briefing-reader\runstate.json`, kept for 14 days; deleting it
only means that the next catch-up may ask about a briefing you already heard. A
`--from-file` test run never writes it.

**The hotkey.** Press **Ctrl+Alt+J** anywhere. If the app is open, it comes
forward and starts reading (from the prompt, or after Later, it is the same as
Read now; on the reading screen it only comes forward). If it is not running, it
starts with `--now` and reads the latest briefing. Presses within 2 seconds of
the previous one are ignored. The "Briefing hotkey" task runs a small agent
(`--hotkey-agent`) from logon until you log off; it waits inside Windows and
uses no CPU until the key is pressed, and it never loads the window code.
Change the combination with `[hotkey] combo` in `config.toml` (ctrl, alt, shift,
win plus one letter, digit or F1 to F24; a letter or digit needs ctrl, alt or
win), or turn it off with `[hotkey] enabled = false`; then rerun
`install-schedule.ps1`, which restarts the agent (or removes it). If another
program already uses the combination, the agent logs a warning and exits (see
[Troubleshooting](#troubleshooting)).

## Calendar actions

The briefing task does not add calendar events itself. It only **proposes**
them, in a "Proposed actions" section of the page (format below), and you
decide in briefing-reader:

- The proposals are not read out line by line. Instead, just before "That's the
  end of your briefing." the app says how many invites need your OK and names
  each one, for example "Two calendar invites need your OK. Project sync, Friday
  October 9, 3 to 4 PM, weekly until December 11. ... Approve or deny them on the
  right." Only proposals you have not decided yet are mentioned.
- Every proposal is a card in the amber **NEEDS YOUR OK** column of the reading
  screen: the kind (CALENDAR), the title and the details, such as
  "Fri Oct 9 · 3:00-4:00 PM · weekly until Dec 11". The prompt's amber chip,
  the STATUS panel and the column title ("2 PENDING") show how many are waiting.
- **Approve** adds the event to Google Calendar. **Deny** dismisses it. Nothing is
  ever created without Approve; Deny, or not deciding at all, never contacts
  Google.
- A card keeps its size when you click: the result (WORKING..., ADDED, DENIED,
  ...) takes the place of the buttons, so the cards below never slide under
  your mouse. A second Approve or Deny click within 1 second of the previous
  one, on any card, is ignored, so a double click cannot decide two proposals.
- One approval at a time: while an Approve is running (also while it waits for
  the Google sign-in), Deny and Approve on the other cards are dimmed and do
  nothing; pointing at them says "Finishing the previous approval..." (during a
  Connect sign-in from TODAY: "Finishing the Google sign-in..."). They work
  again as soon as it is done, whether it worked or failed. Clicks on dimmed
  buttons still count for the 1-second rule above, so a burst of clicks that
  lasts past the end of an approval decides nothing more; click again once
  the mouse has rested for a second.

What Approve does:

1. If you have not signed in to Google on this PC yet, your browser opens the
   Google sign-in and the card shows WAITING FOR GOOGLE SIGN-IN (setup step 8).
2. The app looks on your calendar for an event with the same title and the same
   start. If there is one, nothing is added and the card shows ALREADY ON
   CALENDAR.
3. Otherwise it creates the event: the title, the start and end in your
   calendar's time zone (or all day), the repeat, the place as location, and the
   notes as the description followed by "Added by briefing-reader from your
   Daily Briefing.", with your default reminders. The card shows ADDED and an
   **Open** link to the event in Google Calendar.
4. If something goes wrong, the card keeps its Approve and Deny buttons, so you
   can try again, and shows FAILED with the reason under them (at most two
   lines; point at it to read the whole reason).

Requests run one at a time in the background (the TODAY panel's reads queue
behind them), so the window stays usable. If you
close the app while one is running, it does not wait for it; if the event was
created anyway, the next Approve of that proposal finds it and shows ALREADY ON
CALENDAR.

Decisions are remembered in `%LOCALAPPDATA%\briefing-reader\actions.json` for 60
days. A proposal is recognised by its title (ignoring case), date, times and
repeat, not by its place or notes, so when a later briefing repeats a proposal
you already approved or denied, its card shows that result and it is not
mentioned again. If the time changes, it counts as a new proposal.

Lines of another kind ("Todo: ...", "Reply: ...") and plain sentences are shown
as cards without buttons, for information only. A calendar line that cannot be
read is shown with the reason and no Approve button.

If `[calendar] enabled = false`, or step 8 was not done, the cards are still
shown, but Approve only shows "Google Calendar is not set up yet - see README
step 8" and sends nothing.

Approvals happen in the reading screen. To approve without listening, click
**Read now** and then **Pause**.

## "Proposed actions" format

The proposals go under a heading named "Proposed actions" (any heading level;
case, emoji and a trailing count such as "(3)" do not matter; `[actions]
heading` in `config.toml` changes the name). The section runs until the next
heading of the same or a higher level. Each bullet is one proposal (numbered
items and plain paragraphs work too):

```
Calendar: <title> | <when> | <repeat> | <where> | <notes>
```

Fields after `<when>` are optional and may be empty (`| |`). Markdown is removed;
a link `[Zoom](https://...)` becomes "Zoom (https://...)" and bare URLs are kept.
Any further `|` after `<notes>` is kept as part of the notes. Examples:

```
Calendar: Project sync | 2026-10-09 15:00-16:00 | weekly until 2026-12-11 | https://meet.google.com/abc-defg-hij | weekly sync, invite from the organizer
Calendar: Club general meeting | 2026-10-06 17:00-18:00 | | Central Library, Room 101 |
Calendar: Essay draft due | 2026-10-05 | | | first draft and peer review
Calendar: Spring break | 2027-03-22 to 2027-03-26
```

| Part | What is accepted |
|---|---|
| kind | `Calendar:` (also `Cal:`, `Event:`, `Invite:`, `Calendar event:`, `Calendar invite:`; any case, bold is fine). Another word (`Todo:`, `Reply:`) makes an information-only card. |
| `<title>` | The event name (required). |
| `<when>` | `YYYY-MM-DD HH:MM-HH:MM` timed (an end before the start means it ends the next day); `YYYY-MM-DD HH:MM` timed, 60 minutes; `YYYY-MM-DD` all day; `YYYY-MM-DD to YYYY-MM-DD` all day over several days, end date included. 24-hour times are preferred; 12-hour times work too (`3:00 PM-4:00 PM`, `3pm-4pm`, `3 PM - 4:30 PM`, `noon-1pm`), with a hyphen or an en dash, spaces optional. A weekday before the date (`Fri 2026-10-09`) is checked against the date. A bare `3` needs AM/PM or `HH:MM`. Times are wall-clock times in your Google Calendar's time zone. |
| `<repeat>` | Empty or `once` (one-off), `daily`, `weekdays` (Monday to Friday), `weekly`, `biweekly` (every 2 weeks), `monthly`, `yearly`; optionally followed by `until YYYY-MM-DD` or `x N` / `N times`. |
| `<where>` | Place, address or meeting link; becomes the event's location. |
| `<notes>` | Becomes the event's description. |

A line that cannot be read becomes a card without Approve that shows the line
and the reason, for example "2026-10-09 is a Friday, not Thu", 'the time "3"
needs AM/PM or HH:MM (24-hour)' or "the repeat ends before the event starts". A
line with `|` but no kind says so. The same proposal listed twice is shown once.

## "Deadlines" format

The briefing can list what is due soon under a heading named "Deadlines" (any
heading level; case, emoji and a trailing count do not matter), usually right
after "Proposed actions". A "Deadlines" subheading inside "Proposed actions"
(or the other way round) works too: each section still goes to its own place.
Like that section, it is neither read aloud nor shown
in the text panel: its lines go to DEADLINES (see
[TODAY and DEADLINES](#today-and-deadlines)). One line per deadline:

```
Deadline: <title> | <YYYY-MM-DD> or <YYYY-MM-DD HH:MM> | <source>
```

```
Deadline: HIST Essay #1 draft | 2026-10-09 | Canvas
Deadline: CS 101 Problem set 3 | 2026-10-07 23:59 | Gradescope
Deadline: Internship application | 2026-10-14 | Email from the recruiter
```

The time is a local 24-hour time (12-hour times such as `11:59 PM` work too); a
time that cannot be read ("EOD") keeps just the date. The source (where it was
found) is optional. Lines that cannot be read are skipped. A page without the
section works; calendar events named like deadlines are still listed.

## Instructions for the Claude briefing task

Paste this block into the instructions of the scheduled task (for example a
Claude scheduled task with Notion access) that writes your briefing page, such
as "Daily Briefing (auto)". It keeps the page in the format briefing-reader
reads best, and it makes the task propose calendar events instead of creating
them. Replace the page name if yours is different.

```text
DAILY BRIEFING PAGE FORMAT
(The page "Daily Briefing (auto)" is read aloud by the briefing-reader app,
which also turns the "Proposed actions" section into calendar invites that the
user approves one by one.)

1. Header. Replace the whole page on every run. The page starts with these
   two lines, as plain paragraphs, before anything else:

   Updated: YYYY-MM-DD HH:MM PDT
   Run: AM

   Use the user's local date and 24-hour time at which you write the page,
   followed by the current time zone abbreviation (for example PDT or PST,
   EDT or EST, UTC) or a UTC offset such as -07:00. Write "Run: AM" on the
   morning run and "Run: PM" on the evening run.

2. Sections. Write every section title as a real Notion heading block
   (Heading 2 or Heading 3; "## " and "### " in Notion markdown), never as bold
   text or a plain paragraph. The current layout is fine: one Heading 2 per
   account ("Work (...)", "Personal (...)") with the Heading 3 sections "Due",
   "Actionable", "Newsletters" and "Ignore" inside each, then Heading 2
   sections such as "Unanswered items" and "Tomorrow's calendar". A count in a
   title, such as "Due (3)", is fine. Items under an "Ignore" heading are only
   read when the user asks for them. No emojis are needed (they are removed
   before reading).

3. Calendar: propose, do not create. Do NOT create, change or delete calendar
   events any more, and do not accept, decline or send invites; reading the
   calendar is still fine. Instead, for every event you would have created, add
   one bullet under a Heading 2 titled exactly "Proposed actions", placed at the
   end of the page. Each bullet is one event, on one line, in exactly this
   format:

   Calendar: <title> | <when> | <repeat> | <where> | <notes>

   <title>   Short event name.
   <when>    Wall-clock time in the time zone of the user's Google Calendar,
             24-hour, in one of these forms:
               YYYY-MM-DD HH:MM-HH:MM    timed event (an end before the start
                                         means it ends the next day)
               YYYY-MM-DD HH:MM          timed event of 60 minutes
               YYYY-MM-DD                all-day event
               YYYY-MM-DD to YYYY-MM-DD  all-day event over several days, end
                                         date included
   <repeat>  Empty for a one-off event, or one of: daily, weekdays, weekly,
             biweekly, monthly, yearly - optionally followed by
             "until YYYY-MM-DD" or "x N" (N occurrences). Propose a recurring
             meeting once, with its repeat, not once per occurrence.
   <where>   Place, address or meeting link; empty if none.
   <notes>   One short line of context (who sent it, what to bring); empty if
             none.

   Plain text only, no "|" inside a field, no line breaks inside a bullet.
   Empty fields stay as "| |"; empty fields at the end may be left out.
   Examples of the format (not real events):

   - Calendar: Project sync | 2026-10-09 15:00-16:00 | weekly until 2026-12-11 | https://meet.google.com/abc-defg-hij | weekly sync, invite from the organizer
   - Calendar: Club general meeting | 2026-10-06 17:00-18:00 | | Central Library, Room 101 |
   - Calendar: Essay draft due | 2026-10-05 | | | first draft and peer review
   - Calendar: Spring break | 2027-03-22 to 2027-03-26

4. Only propose events that are not on the calendar yet (an invite that Google
   already put on the calendar needs no proposal). When a later run proposes
   the same event again, keep its title, date, times and repeat exactly the
   same, so the user's earlier Approve or Deny is remembered. If there is
   nothing to propose, leave the "Proposed actions" heading out. There is no
   need to list the proposals anywhere else on the page: the app reads them out
   and the user approves or denies each one.

5. Deadlines. After "Proposed actions", add a Heading 2 titled exactly
   "Deadlines" with one bullet per thing due in the next 14 days that you
   found in email or on the calendar (assignments, exams, applications,
   registrations, forms), in exactly this format:

   Deadline: <title> | <YYYY-MM-DD> or <YYYY-MM-DD HH:MM> | <source>

   <title>   Short name of what is due.
   <date>    The due date in the user's time zone; add the 24-hour time when
             one is given.
   <source>  Where it came from, e.g. "Canvas", "Email from the recruiter".

   The app shows these in its DEADLINES list and does not read them aloud.
   If nothing is due, leave the "Deadlines" heading out.
```

## Command line

```
py -3.13 -m briefing_reader [--run {am,pm} | --catch-up | --hotkey-agent] [--slots am=HH:MM,pm=HH:MM]
                            [--now] [--from-file PATH] [--debug] [--version]
```

| Option | Meaning |
|---|---|
| `--run am` / `--run pm` | Expect that run (case-insensitive). Polls the page every 60 s for up to 15 min until "Updated" is from today and "Run" matches; if it never does, reads what is there with a spoken note that it may be stale. Within 3 hours after the run's scheduled time (`--slots`), the day of that time also counts, so a PM start at 00:30 accepts the page written at 23:42. This is what the scheduled tasks use. |
| `--catch-up` | What the catch-up task runs at logon and unlock: if a scheduled briefing passed in the last 3 hours, was not answered and the app is not open, behave exactly like `--run` for that run; otherwise exit at once (see [Catch-up and the hotkey](#catch-up-and-the-hotkey)). |
| `--hotkey-agent` | What the hotkey task runs at logon: listen for the `[hotkey]` combination until logoff. |
| `--slots am=10:12,pm=23:42` | The scheduled times (24-hour), which tell the app which briefing an answer belongs to. The tasks pass them; without it (or for a run it leaves out) `[schedule]` in `config.toml` applies. A value that cannot be read is logged and ignored. |
| `--now` | Skip the prompt: fetch and read immediately (no waiting for a fresh page). |
| `--from-file PATH` | Developer/testing option: load a saved Notion API fixture instead of calling Notion; no token or page id needed, and answers are not recorded for the catch-up. Example: `py -3.13 -m briefing_reader --now --from-file tests\fixtures\fake_page.json` |
| `--debug` | More detailed log. |
| `--version` | Print the version and exit. |

Without `--run`, the app shows the prompt and fetches the page (retrying if Notion
cannot be reached) but does not wait for a fresh one; a briefing that is not from
today still gets the spoken stale note.
Starting the app while it is already running brings the running window forward
instead of opening a second one (with `--now` it switches to reading; with a
different `--run`, a prompt that is still waiting switches to that run, so the PM
task taking over an AM window that was never answered asks about the PM briefing;
the same `--run` again waits for that run anew if the page shown is not fresh).
If the reading screen is open, a run whose briefing is not the one on screen
brings back the prompt for it; while the briefing is still playing or paused, the
reading screen says "Your AM briefing is due" instead (in the STATUS panel), and
the prompt for it comes when the reading ends or you click Done.

## Configuration

**`.env`** (next to this README; start from `.env.example`): `NOTION_TOKEN`
and `BRIEFING_PAGE_ID`, both required. `BRIEFING_PAGE_ID` is the page the app
reads: the 32-character id, the dashed form, or the page URL (setup step 3).
There is no default page; when the id is missing or cannot be read, the app
shows "Notion page ID missing." and fetches nothing. Variables already set in
the environment win over `.env`.

**`google_client_secret.json`** (next to this README, optional): the Google OAuth
client for calendar actions (setup step 8).

**`config.toml`** (next to this README) holds everything else. Every key is
optional. A bad value never stops the app: an invalid value is replaced by the
default, a number outside its range is clamped, and a warning is written to the
log. Changes apply the next time the app starts.

| Key | Default | Meaning |
|---|---|---|
| `[voice] voice` | `"en-US-GuyNeural"` | Online edge-tts voice. List them with `py -3.13 -m edge_tts --list-voices`. |
| `[voice] rate` | `"+0%"` | Speed, a signed percentage from `"-50%"` to `"+100%"`, e.g. `"+15%"` faster, `"-10%"` slower. |
| `[voice] volume` | `"+0%"` | Loudness, `"-100%"` to `"+100%"`, e.g. `"-20%"` quieter. |
| `[voice] offline_voice` | `""` | Part of a Windows voice name for the offline fallback, e.g. `"Zira"` or `"David"`; empty = Windows default. List them with `py -3.13 -c "import pyttsx3; [print(v.name) for v in pyttsx3.init().getProperty('voices')]"`. |
| `[voice] section_gap_ms` | `600` | Silence between sections (0-10000 ms). |
| `[voice] divider_pause_ms` | `900` | Silence for a divider line (0-10000 ms). |
| `[prompt] later_short_minutes` | `10` | First Later button, also used when the prompt is ignored (1-1440). |
| `[prompt] later_long_minutes` | `30` | Second Later button (1-1440). |
| `[prompt] ignore_after_seconds` | `120` | Unanswered prompt counts as the short Later after this (10-3600). |
| `[prompt] refocus_on_reprompt` | `false` | `true`: prompts after Later also take keyboard focus. |
| `[polling] interval_seconds` | `60` | How often `--run` checks the page (10-3600). |
| `[polling] timeout_minutes` | `15` | How long `--run` waits for a fresh page (1-720). |
| `[sections] ignore` | `["Ignore"]` | Sections skipped unless you click Read everything. Matching ignores case, emoji and a trailing count, and catches headings that start with the name ("Ignore (14)", "Ignored"). A toggle or callout with such a title (its contents are skipped) and a short paragraph used as a title ("Ignore (2)", "Ignore:") count too. `[]` reads everything. |
| `[sections.announce]` | the four headings | `"Heading on the page" = "What to say"`. Headings not listed are announced as written. |
| `[notion] version` | `"2022-06-28"` | Notion API version header. |
| `[calendar] enabled` | `true` | `false`: proposals are still shown, but Approve never contacts Google. |
| `[calendar] client_secret` | `"google_client_secret.json"` | The OAuth client file from step 8: a path relative to the project folder, or an absolute path. |
| `[calendar] calendar_id` | `"primary"` | The calendar new events go to. `"primary"` is your main calendar; another calendar's id is in Google Calendar under that calendar's Settings > Integrate calendar. |
| `[actions] heading` | `"Proposed actions"` | The heading the proposals are under: one name, or a list such as `["Proposed actions", "Actions"]`. Matching ignores case and a trailing count. |
| `[schedule] am` | `"10:12"` | Time of the AM task (24-hour `HH:MM`); the default is only an example, set it a few minutes after your own briefing task runs. `install-schedule.ps1` uses it unless you pass `-AmTime`; the app uses it to tell which briefing an answer belongs to when it was started without `--slots`. Rerun the script after a change. |
| `[schedule] pm` | `"23:42"` | Time of the PM task, the same way (`-PmTime`). |
| `[hotkey] enabled` | `true` | `false`: `install-schedule.ps1` does not install the hotkey task (and removes an existing one); an agent that is started anyway exits at once. |
| `[hotkey] combo` | `"ctrl+alt+j"` | The global hotkey: `ctrl`, `alt`, `shift`, `win` plus one letter, digit or `F1`-`F24`, e.g. `"ctrl+shift+F9"`. A letter or digit needs ctrl, alt or win. Rerun `install-schedule.ps1` (or log off and on) after a change. |
| `[agenda] evening_from_hour` | `18` | From this hour on, the TODAY panel shows tomorrow (0-24; 24 = always today). |
| `[agenda] deadline_days` | `14` | How many days ahead DEADLINES looks (1-60). |
| `[agenda] calendars` | `["primary"]` | The Google calendars listed in TODAY: `"primary"` or calendar ids (see `[calendar] calendar_id`). |
| `[agenda] deadline_keywords` | `["due", "deadline", "exam", "midterm", "final", "quiz", "submit", "submission", "assignment", "lab report", "application"]` | A calendar event in the next `deadline_days` days whose title contains one of these words (whole words, any case) is also listed under DEADLINES. `[]` turns that off. |
| `[display] clock` | `"12h"` | How the header clock, TODAY, ACTIVITY, STATUS and the Intro line of the SECTIONS list show times: `"12h"` (1:05 PM) or `"24h"` (13:05). Times inside sentences ("Updated today at 10:04 AM", "asking again at 1:15 PM", "until 1:20 PM" on the prompt, in the tray and on the reading screen), the proposal cards, the DEADLINES due labels and the spoken briefing always use 12-hour times. |

The offline voice uses the same `rate` and `volume`: the rate scales Windows'
default 200 words per minute, and a volume above `+0%` cannot get louder than
full volume.

## How the page is read

- **Header lines.** The first lines of the page are searched for `Updated: ...`
  and `Run: AM` / `Run: PM` (they may also share one line; bold markers are fine).
  These lines are not read aloud. The timestamp looks like
  `2026-10-04 10:04 PDT`; seconds, AM/PM, a zone abbreviation (UTC, GMT, EST/EDT,
  CST/CDT, MST/MDT, PST/PDT, AKST/AKDT, HST) or an offset like `-07:00` are
  understood. Without a known zone it is taken as this PC's local time.
  `Updated: never` shows as "Not updated yet".
- **Freshness.** A briefing is fresh when its Updated date is today (in this PC's
  time zone) and its Run matches the one asked for with `--run`. "Today" also
  includes the day the run started, so a PM briefing that was fresh at 23:42 is
  not called stale when you answer the prompt after midnight. With `--run` the
  app waits for a fresh page as described above; if it never arrives, it reads
  what is there and starts with a note such as "Heads up: this briefing may be
  stale. It was last updated yesterday at 11:31 PM." The prompt shows the Updated
  time in every case.
- **Sections.** The largest headings on the page start sections; smaller headings
  inside a section are read as subheadings. If the page starts with one larger
  heading that works as a title (for example a heading 1 "Email triage - Sun Oct
  4" above heading 2 sections), it is read once at the start and the next level
  starts the sections. Each section is announced by name ("Work inbox", "Due",
  "Actionable", "Newsletters", or whatever the page uses); a count such as "Work
  inbox (3)" is spoken as "Work inbox. 3 items." The reading starts with "Here's
  your AM briefing, updated today at 10:04 AM." and ends with "That's the end of
  your briefing."
- **Proposed actions.** The "Proposed actions" section (see
  [the format](#proposed-actions-format)) is taken out of the reading: its lines
  are neither read nor shown in the text panel. They become cards in NEEDS YOUR
  OK, and a short "Needs your OK" part just before the end names the ones you
  have not decided yet.
- **Deadlines.** The "Deadlines" section (see [the format](#deadlines-format))
  is taken out of the reading the same way; its lines are listed under
  DEADLINES on the left.
- **Titles that are not headings.** Ask the scheduled task to write every section
  title as a Notion heading (`## Work inbox (3)` in Notion markdown); that is the
  format read most reliably. Short paragraphs are also taken as section titles:
  on a page without any headings, a paragraph that is bold as a whole or starts
  with `## `; on any page, a paragraph named like an ignore section ("Ignore (2)",
  "Ignore:") or like a heading in `[sections.announce]` ("Due:").
- **Ignore section.** Skipped unless you click Read everything (see `[sections]`).
  A toggle (or callout) titled "Ignore" counts as an ignore section holding the
  items inside it; whatever follows the toggle is read again.
- **Dividers** become pauses: real silence inserted by the player
  (`divider_pause_ms`), because the voice does not pause for "...".
- **Text for listening.** Markdown, links (the label is kept), bare URLs, HTML
  and emoji are removed. Bullets and long lines become short sentences (split at
  spaced dashes, `|`, `;` and `·`), but a spaced dash between two times, prices
  or days is a range ("2pm - 3pm", "$40 - $60" and "Oct 12 - 15" read "to"), "&"
  is read as "and", arrows as "to" (or as a
  new sentence), "w/", "e.g.", "i.e." and "etc." are spelled out, day and month
  abbreviations become full names ("Fri" becomes "Friday"), "Re:"/"Fwd:" prefixes
  are dropped, and checked to-dos are read as "Already done: ...". Nested list items
  are indented on screen. Code blocks are shown but not read; images, files and
  bookmarks only by their caption; child pages and databases only by their title.

## Audio player choice

The app needs one mp3 player library; the pick is **QtMultimedia**
(`QMediaPlayer`), which ships inside PySide6, a dependency the window needs
anyway. So `requirements.txt` has no separate player package. Reasons:

- It is non-blocking and driven by the Qt event loop the UI already runs; no
  extra thread or polling loop.
- It reports the playback position (about every 50 ms), which drives the
  highlight, and supports pause, resume and stop.
- It plays both the edge-tts mp3 files and the WAV files of the offline voice.
- Its FFmpeg backend is bundled in the PySide6 wheels (PySide6-Addons), so there
  is nothing else to install.

The usual alternatives fit less well: `playsound` blocks by default, has no pause
or position reporting and is no longer maintained; `pygame` adds SDL and its own
event handling, which would have to be polled next to Qt's loop; `python-vlc`
needs VLC installed separately.

## Offline fallback

edge-tts uses Microsoft's online read-aloud service: it needs internet but no
account, key or payment. If it fails (for example offline), the section is
spoken with a Windows built-in voice through pyttsx3 (SAPI). The prompt then
says "Audio ready (offline voice)", the voice chip in the header turns amber and
the STATUS panel shows Voice as offline. After a failure the app stays on the
offline voice for 2 minutes, then tries edge-tts again. With the offline voice
the highlight is estimated from the text length instead of word timings, so it
is less exact.

## Logs and saved files

`%LOCALAPPDATA%\briefing-reader\logs\briefing-reader.log`, rotated at 1 MB with 5
old files kept (`briefing-reader.log.1` to `.5`). If that folder cannot be
created, the log goes to `%TEMP%\briefing-reader\logs`. The catch-up launches and
the hotkey agent write to the same file (the agent opens it only for each line,
so it never keeps the app from rotating it). Run with `py` instead of
`pythonw` to see the log in the console too, and add `--debug` for more detail. The
Notion token is never logged; as a safeguard, the token and anything shaped like
a Notion secret are also replaced with `[REDACTED]` before a line is written. The
same applies to the Google client secret and the Google access and refresh
tokens: they are registered for redaction as soon as they are read. The briefing
text itself is not logged at the normal level; calendar entries in the log name
only action ids and Google event ids, and the TODAY / DEADLINES panels log only
counts, never titles.

Other files in `%LOCALAPPDATA%\briefing-reader`:

| File | What it is |
|---|---|
| `actions.json` | Your Approve / Deny decisions (and the last failure message), kept for 60 days. Delete it to forget them. |
| `google_token.json` | The Google sign-in (access and refresh token). Private: never share it. Delete it to sign out on this PC. |
| `runstate.json` | Per scheduled briefing: when its prompt was first shown and when and how it was answered (read, dismissed, done), kept for 14 days. The catch-up task uses it; see [Catch-up and the hotkey](#catch-up-and-the-hotkey). Safe to delete. |

Generated audio goes to `%TEMP%\briefing-reader\session-<process id>` and is
deleted when the app closes. If a section is still being generated at that
moment, the (already hidden) app waits up to 15 seconds for it to finish and then
deletes the folder; leftovers older than 12 hours are removed at the next start.

## Privacy and cost

The app talks to three services, all free:

- the Notion API: it only reads the one page, with a read-only integration;
- Microsoft's Edge read-aloud service, which receives the briefing text to turn
  it into speech;
- the Google Calendar API, and only once you have signed in with **Approve**
  or **Connect**: an Approve reads your calendar's time zone, searches your
  calendar for the same event around its start, and sends the event (title,
  times, repeat, place, notes). Nothing is sent to Google before you sign in,
  and events are only read, never changed.

**Reading your calendar.** Once Google Calendar is connected on this PC, every
time the reading screen opens the app reads your calendar events from the start
of today through the next 14 days (`[agenda] deadline_days`; at least through
tomorrow) for TODAY / TOMORROW and DEADLINES, without a click, and reads them
again every 10 minutes while the reading screen is visible. The events (titles,
times, places) are only shown in that window on this PC; they are not saved,
and the log gets only their number. To stop it, set `[calendar] enabled =
false` in `config.toml` (this also turns off Approve) or sign out by deleting
`%LOCALAPPDATA%\briefing-reader\google_token.json` (see "Disconnecting" in
setup step 8).

There are no paid API calls of any kind. The offline voice runs entirely on this
PC.

## Fonts

The HUD uses three open-source fonts, bundled in `fonts\` and loaded by the app
when it starts (nothing is installed into Windows):

| Font | Used for | Copyright | Licence |
|---|---|---|---|
| Chakra Petch (Medium, SemiBold) | JARVIS wordmark | 2018 The Chakra Petch Project Authors (<https://github.com/m4rc1e/Chakra-Petch>) | `fonts\OFL-ChakraPetch.txt` |
| Sora (variable) | text, the spoken line | 2019 The Sora Project Authors (<https://github.com/sora-xor/sora-font>) | `fonts\OFL-Sora.txt` |
| JetBrains Mono (variable) | labels, times, status | 2020 The JetBrains Mono Project Authors (<https://github.com/JetBrains/JetBrainsMono>) | `fonts\OFL-JetBrainsMono.txt` |

All three are licensed under the SIL Open Font License, Version 1.1
(<https://openfontlicense.org>). Keep each licence file next to its font when you
copy or share the folder. If a font file is missing, the app logs it and uses
Windows fonts instead (Bahnschrift, Segoe UI, Cascadia Mono or Consolas).
See also [License](#license).

## Tests

```powershell
py -3.13 -m unittest discover -v
```

The tests use saved fake Notion responses (`tests\fixtures`, with an invented
page and invented people, places and courses) and fake Google Calendar
services, and never call Notion or Google; they need no `.env`. The clock display
tests run Qt offscreen, so no window appears. The live speech tests are
skipped unless you opt in; they synthesize a short text with edge-tts (needs
internet) and with the Windows voice into a temporary folder, and play nothing:

```powershell
$env:BRIEFING_LIVE_TTS = "1"; py -3.13 -m unittest tests.test_tts -v; Remove-Item Env:BRIEFING_LIVE_TTS
```

The hotkey tests register Ctrl+Alt+Shift+F24 (a key no keyboard has) for a
moment with a private agent lock, simulate a press without pressing anything,
and release it within seconds; they never start the app. The catch-up tests run
in a separate Python with a temporary `LOCALAPPDATA` and without reading `.env`.

## Troubleshooting

- **"Notion rejected the token (401)"**: the secret in `.env` is wrong or was
  regenerated. Copy it again from the integration page into `NOTION_TOKEN=`.
- **"...not found or is not shared with the integration (404)"**: share the page
  with the integration (setup step 3), and check that `BRIEFING_PAGE_ID` is that
  page's id.
- **"Notion token missing."**: `.env` is missing, empty, or not in the project
  folder next to `README.md`.
- **"Notion page ID missing."**: `BRIEFING_PAGE_ID` in `.env` is missing, empty
  or not a page id or Notion page link (the log then says "BRIEFING_PAGE_ID is
  not set" or "...is not a Notion page id or page URL"). Copy the page's link
  again (setup step 3) and paste it after `BRIEFING_PAGE_ID=`.
- **No sound**: check the Windows default output device and its volume; the app
  plays on the default device and follows it when it changes. Look in the log for
  playback or speech errors.
- **The task did not fire**: open Task Scheduler (`taskschd.msc`) > Task Scheduler
  Library > Briefing AM and check Last Run Result and the History tab (if History
  is empty, "Enable All Tasks History" in the right pane turns it on; that needs
  administrator rights). Check the wake timers (rerun `install-schedule.ps1 -DryRun`).
  The task does not run when the PC was shut down or you were signed out; the
  catch-up task then asks when you log on or unlock within 3 hours. To see
  errors, reinstall with `-Console` or run `py -3.13 -m briefing_reader --run am
  --debug` by hand.
- **The prompt came late after the PC woke up**: check in Task Scheduler that the
  tasks start `pythonw.exe` (the Actions tab), not `pyw.exe`; rerun
  `install-schedule.ps1` (without `-UseLauncherAlias`) if they do not. The log
  shows when Python actually started ("briefing-reader ... starting").
- **No prompt after unlocking, although a briefing was missed**: the catch-up
  only asks about the latest scheduled briefing, only within 3 hours of it, and
  only when it was not answered and the app is not open. Its decision is in the
  log, in a line starting with "Catch-up:". Check that "Briefing catch-up" exists
  in Task Scheduler (rerun `install-schedule.ps1`).
- **The hotkey does nothing**: look in the log for "Could not register the hotkey
  ...: another program probably uses it". Then pick another combination with
  `[hotkey] combo` in `config.toml` (for example `"ctrl+alt+shift+j"` or
  `"ctrl+alt+F9"`) and rerun `install-schedule.ps1`. If the log says nothing about
  the hotkey, check that "Briefing hotkey" exists and is Running in Task
  Scheduler (rerun `install-schedule.ps1`, which starts it, or right-click it >
  Run). Some programs that run as administrator (or full-screen games) keep keys
  to themselves while they are in front.
- **The prompt is hidden**: click the tray icon, or run the command again
  (`py -3.13 -m briefing_reader`), which brings the running instance forward.
  The hotkey brings it forward too, but starts reading right away.
- **No proposals show up**: the heading must be named "Proposed actions" (or what
  `[actions] heading` says) and each line must start with `Calendar:`; see
  [the format](#proposed-actions-format). Lines that cannot be read still show
  as cards, with the reason.
- **TODAY says "Connect Google Calendar to see your day"**: this PC has no
  saved Google sign-in (it is kept per PC), or Google revoked it. Click
  **Connect**. **"Couldn't load the calendar"**: point at the line for the
  reason; "(404: Not Found)" means a calendar id in `[agenda] calendars` is
  wrong (`"primary"` always works). It is tried again every 10 minutes.
- **A deadline is missing**: DEADLINES shows at most 8, only the next 14 days
  (`[agenda] deadline_days`), and only page lines in
  [the format](#deadlines-format) or calendar events whose title has a word
  from `[agenda] deadline_keywords`.

**Google Calendar and sign-in** (the cards show their messages in capitals;
they are quoted here in normal case, and the log has the same text)

- **"Google Calendar is not set up yet - see README step 8"**: `[calendar]
  enabled` is `false`, or `google_client_secret.json` is missing. Do setup
  step 8.
- **"Set up Google Calendar: README step 8 (...)"**: the reason is in brackets.
  "was not found": the file is not in the project folder or not named
  `google_client_secret.json` (Windows may hide the `.json` ending; check for
  `google_client_secret.json.json`). "is not valid JSON": download it again.
  "is not an OAuth client of type Desktop app": the client was created as a Web
  application or another type; create one of type **Desktop app**. "the Google
  packages are missing": run setup step 1 again. "the Google Calendar API is not
  enabled": enable it (step 8.2) and wait a few minutes.
- **"Google hasn't verified this app"** on the sign-in page: expected for your
  own client. Check that it names briefing-reader and your own email as the
  developer, then click **Advanced** > **Go to briefing-reader (unsafe)**.
- **"Access blocked: briefing-reader has not completed the Google verification
  process" (Error 403: access_denied)**: the app is still in **Testing** and the
  account you picked is not a test user. Add it under **Audience > Test users**,
  or set the publishing status to **In production** (step 8.4).
- **Access blocked with a school or work account**: the organisation may block
  apps it has not approved. Sign in with your personal Google account instead.
- **Asked to sign in again every week, or "FAILED: Google Calendar sign-in
  expired or was revoked. Approve again to sign in."**: an app in **Testing**
  gets sign-ins that expire after 7 days. Set the publishing status to **In
  production** (step 8.4) and click Approve again: it signs in once more, and
  that sign-in does not expire. The same message appears after you removed the
  app's access in your Google account; the app then deletes its saved sign-in
  by itself.
- **"FAILED: Google sign-in timed out"**: the sign-in in the browser was not
  finished within 5 minutes. Click Approve again. If no browser tab opened,
  look for it behind other windows or in another browser window.
- **"FAILED: Google sign-in was cancelled or access was denied"**: you clicked
  Cancel on Google's page. Click Approve again to retry.
- **"FAILED: Google sign-in did not grant every permission"**: Google's page
  may show a checkbox per permission; tick both, then click Approve again.
- **"FAILED: Google Calendar error while ... (404: Not Found)"**: check
  `[calendar] calendar_id` in `config.toml` (`"primary"` always works).
- **The event is at the wrong time**: times are read as wall-clock times in your
  Google Calendar's time zone (Google Calendar > Settings > Time zone). If it
  cannot be read, the app uses this PC's time zone (Windows Settings > Time &
  language; UTC if Windows does not name one) and logs a warning.
- **Revoking access**: open <https://myaccount.google.com/permissions>, pick
  briefing-reader, remove its access, and delete
  `%LOCALAPPDATA%\briefing-reader\google_token.json`.
  If you only revoke on Google's side, the next Approve fails once with the
  "expired or was revoked" message and the app deletes the file itself.

## Project layout

```
briefing_reader/__init__.py   version and app name
briefing_reader/__main__.py   command line, single-instance lock, hand-off out of the scheduled task, catch-up, app start
briefing_reader/activation.py "come forward" messages between launches (Qt local socket), Qt log forwarding
briefing_reader/runstate.py   scheduled slots, which briefings were answered (runstate.json), the catch-up window
briefing_reader/hotkey.py     global hotkey agent (RegisterHotKey), press handling, activation pipe
briefing_reader/config.py     .env, environment and config.toml loading, paths, logging with secret redaction
briefing_reader/models.py     shared data types (flattened lines, script, audio)
briefing_reader/notion_client.py  Notion REST client, block flattener, header parsing, freshness, polling, fixtures
briefing_reader/text_prep.py  markdown stripping, text for listening, script with sections, stale note and Needs your OK part
briefing_reader/actions.py    "Proposed actions" parsing (Calendar: lines), saved Approve/Deny decisions
briefing_reader/agenda.py     TODAY / TOMORROW rows, the "Deadlines" section, calendar deadlines, due labels
briefing_reader/gcal.py       Google Calendar: OAuth sign-in, duplicate check, event creation, reading events
briefing_reader/tts.py        edge-tts synthesis, Windows SAPI fallback, highlight timing, background worker
briefing_reader/player.py     QtMultimedia player that plays sections in order with pauses
briefing_reader/hud.py        Jarvis HUD widget kit: colours, fonts, chamfered panels, orb, buttons, cards, agenda
briefing_reader/ui.py         prompt and reading windows, tray icon, approvals, TODAY / DEADLINES, app controller
fonts/                        Chakra Petch, Sora and JetBrains Mono fonts with their OFL licences
tests/                        unit tests and saved fake Notion pages (tests/fixtures)
config.toml                   voice, prompt, polling, section, calendar, actions, schedule, hotkey and agenda settings
.env.example                  template for .env (NOTION_TOKEN, BRIEFING_PAGE_ID)
requirements.txt              Python dependencies
install-schedule.ps1          registers the Briefing AM / PM / catch-up / hotkey scheduled tasks
uninstall-schedule.ps1        removes them
LICENSE                       MIT licence of the code (the fonts keep their own OFL licences)
.env                          your Notion token and page id (step 4; not in git, keep private)
google_client_secret.json     your Google OAuth client (step 8; not in git, keep private)
```

## License

The code is released under the MIT License; see [`LICENSE`](LICENSE).

The bundled fonts in `fonts\` are not covered by that licence: Chakra Petch,
Sora and JetBrains Mono are each licensed under the SIL Open Font License,
Version 1.1, and their licence files are next to them (`fonts\OFL-ChakraPetch.txt`,
`fonts\OFL-Sora.txt`, `fonts\OFL-JetBrainsMono.txt`; see [Fonts](#fonts)).
