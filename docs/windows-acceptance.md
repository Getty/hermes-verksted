# Windows acceptance scenarios for Verksted step 1 (k4)

Manual scenarios for the maintainer (or a Claude Code session on the Windows box). They are the
real acceptance of card k4 and of the Windows rows of `docs/architecture.md` §7 that the step-1
prototype (k3) touches. Nothing here has been run on Windows. Each scenario names the Linux
stand-in test that mirrors it (`tests/e2e/test_verksted_e2e.py` and `tests/e2e/test_verksted_k11_e2e.py`,
run with `.dev/venv/bin/python -m pytest tests/e2e -v`; real Hermes backend at `ad2b705`, the Desktop
window is a Node shim); a green stand-in says nothing about drive letters,
the real Desktop renderer, the packaged app or a console window. Report results as
"exercised on Windows build `<commit>`", per scenario, pass / fail / not run.

Fixed vocabulary in the Desktop part (German, from `verksted/desktop/plugin.js`):
"Rückfrage", "Freigabe erforderlich", "Session öffnen", "wird gestartet", "läuft", "fertig",
"abgelehnt", "unbekannt", "wartet auf Hermes Desktop".

## 0 Preconditions (once)

1. **Record the build** as in `docs/hermes-interfaces.md` "Windows check for the maintainer" §1–2:
   Desktop version and install stamp, backend version and commit, native or WSL backend. The
   package was written against Hermes `ad2b705` / `v0.22.6`; on any other build, say so in the
   report and expect interface drift (re-run that document's §5 console checks first).
2. **A throwaway Desktop home, not the real one.** Quit the Desktop, then start it with
   `HERMES_HOME=C:\verksted-k4\home` and `HERMES_DESKTOP_USER_DATA_DIR=C:\verksted-k4\desktop`
   (`docs/hermes-interfaces.md` Windows check 4a). If the packaged app ignores the variables,
   stop: installing into the maintainer's own Hermes home or Desktop (`%LOCALAPPDATA%\hermes`)
   needs the maintainer's explicit go-ahead. A Claude Code session must ask first.
3. **Install the package** (copy, not symlink, on Windows). From a release zip
   (`make package` on the Linux box writes `dist\verksted-<version>.zip`; its top folder is
   `verksted\`), in PowerShell with the two throwaway variables set (the same shell later starts
   the Desktop, so it inherits them):
   ```powershell
   $env:HERMES_HOME = 'C:\verksted-k4\home'
   $env:HERMES_DESKTOP_USER_DATA_DIR = 'C:\verksted-k4\desktop'
   New-Item -ItemType Directory -Force "$env:HERMES_HOME\plugins" | Out-Null
   # update: remove the old copy first, so no file of an older build survives
   Remove-Item -Recurse -Force "$env:HERMES_HOME\plugins\verksted" -ErrorAction SilentlyContinue
   Expand-Archive .\verksted-0.1.0.zip -DestinationPath "$env:HERMES_HOME\plugins"   # yields plugins\verksted\
   Get-ChildItem "$env:HERMES_HOME\plugins\verksted" -Recurse -Force |
     Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }          # must print nothing (no links)
   # update: the Desktop half is copied once and kept while its mtime looks current - delete it
   # BEFORE the next Rescan, or an old plugin.js stays (scenario S)
   Remove-Item -Recurse -Force "$env:HERMES_HOME\desktop-plugins\verksted" -ErrorAction SilentlyContinue
   ```
   Without a zip, copy the repo's `verksted\` folder to `$env:HERMES_HOME\plugins\verksted`
   (`Copy-Item -Recurse`). Do not run `hermes plugins enable` or `hermes plugins install` (they
   re-resolve the whole dependency environment into the home); edit `C:\verksted-k4\home\config.yaml`
   instead:
   ```yaml
   plugins:
     enabled: [verksted]
   approvals:
     mode: manual      # a dangerous command must prompt (scenario B)
     timeout: 300      # scenario P shortens it to 20
   tools:
     tool_search:
       enabled: "off"  # optional: without it the main agent reaches `verksted` only via
                       # tool_search -> tool_call (plugin tools are deferred at ad2b705)
   ```
   Keep the `_config_version` Hermes wrote. Restart the gateway (`hermes gateway restart --detach`
   in that shell) so the agent half and `dashboard/plugin_api.py` load. Packaged-app check: if
   the Desktop ignores the two variables, stop (see step 2).
4. **Enable the Desktop half**: Capabilities, Plugins, Rescan, then "Verksted" on (it is opt-in, per window
   origin). Both halves are needed; without the Desktop half a run stays "wartet auf Hermes Desktop".
5. **Test folder with space and umlaut**: create `C:\verksted k4\Ärger\work` (and, for scenario D,
   a second folder `C:\verksted k4\Ärger\work2`). A model that can follow instructions is needed;
   the prompts below are explicit on purpose.
6. Open DevTools (Ctrl+Shift+I) once, to read console errors and `Verksted` warnings; watch the
   taskbar for new console windows throughout.

Start each scenario in a fresh parent chat of the Desktop (the "parent").

## A Happy path, folder with space and umlaut, no console window

- **Covers:** §7 "Windows-Ordner mit Leerzeichen und Umlaut" (file operation lands in the right
  folder; no new console) and §7.1 "Ein Tool-Aufruf erzeugt einen gespeicherten Auftrag; die
  Desktop-Ergänzung startet ... eine Session in einem Testordner".
- **Linux stand-in:** `test_1_happy_path`.
- **Steps:**
  1. In the parent chat: *"Use the verksted tool: action run, folder `C:\verksted k4\Ärger\work`,
     task: create a file `result.txt` containing the word `hallo` in that folder, then answer
     with one sentence. Then tell me the tool's result."* (If the agent cannot find the tool, say:
     use tool_search to find `verksted`, then tool_call.)
  2. Watch the status bar chip and the Verksted page: "wird gestartet", then "läuft", then "fertig".
  3. Check `C:\verksted k4\Ärger\work\result.txt`.
- **Expected:** the parent gets a structured result (`state: finished`, `outcome: completed`,
  `result_block`, `summary` and `outputs` from the child's closing `<verksted-result>` block, plus
  the clipped final answer in `result`; k10). If the child named `result.txt`, it is in `outputs`
  with its relative path. `result.txt` is in `Ärger\work` and nowhere else; the child session is
  listed in the sessions list (it is a `gui` session at this Hermes: the SDK replaces `source`);
  no console window appeared at any step; no `Verksted` error in DevTools.
- **Record:** the run `handle`, the child session id, the exact path the file landed in.

## B Rückfrage through "Session öffnen" in the native child chat

- **Covers:** §7.1 "Eine Freitextfrage ... über den nativen Kindchat" and the invariant that the
  user, not the plugin or the main agent, answers (verksted-core 9).
- **Linux stand-in:** `test_2_rueckfrage_answered_by_the_user`.
- **Steps:**
  1. Parent: *"Use the verksted tool to run, in `C:\verksted k4\Ärger\work`, this task: before doing
     anything, ask the user (clarify tool, free text, no choices) what the project is called, then
     write that name into `name.txt`. Wait for the result."*
  2. The tool call returns early (or `wait` is needed) with state `running` and "Rückfrage"; the
     status chip shows "Verksted: Rückfrage". Do **not** tell the parent anything.
  3. On the Verksted page click "Session öffnen" on that run. The child opens in a tab of the
     connection and profile that started it.
  4. Answer the question in that native chat.
  5. Back in the parent: *"Wait for the verksted run."*
- **Expected:** the question card is the normal Desktop clarify card in the child chat; after the
  answer the run goes to "fertig"; `name.txt` holds the typed name; the parent's result mentions it.
  The Verksted DevTools log shows no `clarify.respond` / `approval.respond` / `clarify.lock`.
- **Also look at:** if you close the child tab or the window **before** the question appears, the
  question may be lost (the child's clarify ends "unanswered" and the turn completes). That is a
  known Hermes behaviour for Desktop-submitted turns (k8), not a failure of this scenario; note
  what you see.

## C Denied approval does not execute

- **Covers:** §7 "Verweigerte Freigabe: Vorgeschlagene Aktion wird nicht ausgeführt"; §7.1
  "eine verweigerte Werkzeugfreigabe müssen über den nativen Kindchat funktionieren".
- **Linux stand-in:** `test_3_denied_approval_does_not_execute`.
- **Steps:**
  1. Parent: *"Use the verksted tool to run, in `C:\verksted k4\Ärger\work`, this task: run exactly
     this terminal command and nothing else: `<DANGEROUS-COMMAND> ; echo done > denied.txt`"*
     (shell syntax of your Hermes terminal backend). Pick a `<DANGEROUS-COMMAND>` that your Hermes
     flags (on Linux `chmod -R 777 .`; on Windows, for example `git reset --hard` in a git repo
     inside the folder); the
     point is an approval prompt plus a file that exists only if the command ran. Confirm that the
     prompt appears before relying on it.
  2. The run shows "Freigabe erforderlich". Open it with "Session öffnen".
  3. In the child chat choose **Deny**.
  4. Parent: *"Wait for the verksted run."*
- **Expected:** `denied.txt` does **not** exist; the run ends `fertig` with outcome `completed`
  (a denied approval still completes at this Hermes); the parent's result does not claim the file
  was written (the child's answer quotes the denial). Control (optional): repeat and choose
  "Approve once" — the file must then exist, proving the command can run on this machine.
- **If the approval is not answered in time** (`approvals.timeout`) Hermes denies it; the run
  must not show "Freigabe erforderlich" for ever (scenario P).

## D Result and resume reach the same parent

- **Covers:** §7.1 "Ergebnis und Wiederaufnahme erreichen dieselbe Elternzuordnung" and §7 "GUI
  schließen und erneut öffnen: Derselbe Auftrag wird abgeglichen; Ergebnis bleibt abrufbar".
- **Linux stand-in:** `test_4_result_reaches_the_same_parent_after_a_new_window` (finished run) and
  `test_4b_takeover_of_a_running_run_reconciles_without_a_second_start` (running run; the lease
  expiry is simulated there, on Windows it is real).
- **Steps:**
  1. Parent: start a run whose child takes a while (*"... task: wait by running `ping -n 40
     127.0.0.1` then write `slow.txt`"*), `wait_seconds` small, so the parent turn ends while the
     run is still `läuft`. Note the `handle` from the result.
  2. Close the Desktop window completely, wait until the claim has lapsed (about 3 minutes; the
     lease is 180 s) while the child keeps running, reopen Desktop on the same throwaway home.
  3. Look at the Verksted page: the same run is listed; it must not show a second run or a second
     child session in the sessions list.
  4. In the **same parent chat** as in step 1: *"Check the verksted run with handle `<handle>` and
     tell me the result."* Then do the same in a **different** parent chat.
- **Expected:** the reopened window reconciles the existing run (no second session, no second
  prompt in the child chat's history); when the child finished, the run is `fertig`; the same
  parent chat gets the result via `status`/`wait`; a different parent chat gets "no Verksted run
  with this handle belongs to this session" (a handle alone grants nothing).
- **Note:** while no window follows the run the Verksted page marks it "(veraltet)" — that means
  the shown state is the last known one, not that the run stopped.

## E Lost start confirmation, no double start (only if reproducible)

- **Covers:** §7 "Zwei GUI-Fenster und verlorene Startbestätigung: Kein automatischer Doppelstart"
  as far as step 1 touches it. Stop at what k3 implements: identical repeat of a lost
  `session.create` reply; claims for two windows are k8.
- **Linux stand-in:** `test_7_lost_create_reply_repeats_the_identical_call` (injected reply loss).
  On Windows the loss cannot be forced from the GUI; **not required** unless you can interrupt the
  loopback connection at the right moment. Record "not run" if so.
- **Optional two-window check:** open a second Desktop window on the same home while a run starts;
  exactly one child session must exist afterwards.

## F Toolset refusal and describe gate (read-only checks)

- **Covers:** §7 "Default, explizite Auswahl, leere und ungültige Toolsets" (the part k3 does).
- **Linux stand-ins:** `test_5_toolset_exceeding_the_listed_set_is_refused_before_submit`,
  `test_5b_listing_the_extra_toolset_passes`, `test_6_route_without_durable_admission_is_refused_before_create`.
- **Steps:** put `.verksted.yaml` with `version: 1` and `toolsets: [file]` into the test folder, run
  a task; then add an enabled plugin toolset to the profile and repeat.
- **Expected:** with an extra enabled toolset the run is "abgelehnt" with reason `toolsets_exceeded`
  naming it, and the child never receives the task (no prompt in the child chat). Listing the extra
  toolset in the file lets the run proceed. The describe gate (`legacy_route`) needs a gateway
  without `durable-admission-v1`; not reproducible on this build, record "not run".

## Path checks on the executing backend (k6)

Scenarios G–K check `docs/architecture.md` §6 "Pfade werden auf dem ausführenden Backend
validiert: Laufwerksbuchstaben, Leerzeichen, Unicode und Junctions ... Bei einem WSL-Backend gilt
dessen Pfadraum". §7 has no row of its own for these; each names the row it is part of. Their
Linux stand-ins in `tests/agent/test_paths.py` and `tests/agent/test_tool.py` (`test_win_*`) run
the rules with `ntpath` and a filesystem stub — simulated, not exercised on Windows. Here the real
`realpath` (`GetFinalPathNameByHandle`) decides.

**Preconditions:** §0 with the package of k5/k6 or later. Every result below is the tool result
the parent reports; ask for it verbatim (*"... and show me the tool's JSON result"*). A refusal is
`ok: false` with `error.code`; an accepted run shows the stored path in `folder`. In every scenario
no console window may appear. For the slow runs use the task *"run `ping -n 60 127.0.0.1`, then
write `slow.txt`"* with `wait_seconds: 0`.

### G Drive-letter case and spelling are one folder

- **§7 row:** "Windows-Ordner mit Leerzeichen und Umlaut" (which folder the run is in); also §4
  "Schreibende Läufe im selben Arbeitsordner werden im MVP serialisiert".
- **Linux stand-in:** `test_win_drive_letter_case_and_trailing_separator_are_the_same_folder`,
  `test_win_one_active_run_per_folder_whatever_the_spelling`.
- **Steps:**
  1. Start a slow run in `C:\verksted k4\Ärger\work`. Note its `handle` and `folder`.
  2. While it runs, same parent: run in `c:\VERKSTED K4\ärger\WORK\` (lower-case drive, other
     case, trailing backslash), task *"write `x.txt`"*.
  3. After the first run is `fertig`, repeat step 2.
- **Expected:** step 1 `folder` is `C:\verksted k4\Ärger\work` exactly (true case, no trailing
  backslash). Step 2 is refused `folder_busy` and carries the first run's `handle`; no second
  child session exists. Step 3 is accepted and its `folder` is again `C:\verksted k4\Ärger\work`,
  not the spelling that was typed.

### H Space and umlaut stay byte-exact, files stay inside

- **§7 row:** "Windows-Ordner mit Leerzeichen und Umlaut" (the file part); scenario A covers the
  file operation itself.
- **Linux stand-in:** `test_win_space_and_umlaut_are_kept_exactly`,
  `test_a_folder_with_space_and_umlaut_keeps_its_bytes`, `test_win_files_stay_inside_the_folder`.
- **Steps:**
  1. Create `C:\verksted k4\Ärger\work\Notiz ü.md` with one line of text.
  2. Run in `C:\verksted k4\Ärger\work` with `files: ["Notiz ü.md"]`, task *"quote the first line
     of the listed file"*.
  3. Run there with `files: ["..\work2\x.md"]`, then with
     `files: ["C:\verksted k4\Ärger\work\Notiz ü.md"]`.
- **Expected:** step 2: the prompt in the child chat ends with the list item `- Notiz ü.md`, the
  child quotes the line, and the Verksted page shows folder and file names without mojibake.
  Step 3: both refused `bad_files` (leaves the folder / not relative); nothing is stored.

### I Junctions into and out of the workspace

- **§7 row:** "Windows-Ordner mit Leerzeichen und Umlaut" (which folder the run is in); §2 "vom
  registrierten Arbeitsbereich bis zum Zielordner".
- **Linux stand-in:** `test_win_short_names_and_junctions_resolve_to_the_real_folder`,
  `test_files_that_leave_the_folder_through_a_symlink_are_refused`,
  `test_registered_workspaces_come_from_the_plugin_settings` (symlink as stand-in for a junction).
- **Steps** (PowerShell):
  1. ```powershell
     New-Item -ItemType Directory 'C:\verksted-k4-outside' | Out-Null
     Set-Content 'C:\verksted-k4-outside\secret.txt' 'secret'
     New-Item -ItemType Junction -Path 'C:\verksted k4\Ärger\work\out' -Target 'C:\verksted-k4-outside'
     New-Item -ItemType Junction -Path 'C:\verksted k4\link-in' -Target 'C:\verksted k4\Ärger\work'
     ```
  2. Start a slow run with folder `C:\verksted k4\link-in`. While it runs, run in
     `C:\verksted k4\Ärger\work`.
  3. After it finished: run in `C:\verksted k4\Ärger\work` with `files: ["out\secret.txt"]`.
  4. Put `.verksted.yaml` with `version: 1` and `toolsets: [file]` into `C:\verksted k4\`. Run
     with folder `C:\verksted k4\Ärger\work\out`.
  5. Register the workspace: Desktop Settings → Plugins → Verksted → `workspaces`
     `["C:\\verksted k4"]` (or in `config.yaml`: `plugins.entries.verksted.settings.workspaces:
     ['C:\verksted k4']`), restart the gateway, repeat step 4.
  6. Clean up: remove the registration, the `.verksted.yaml` of step 4 and both junctions
     (`Remove-Item` on the junction, never on its target's contents).
- **Expected:** step 2: the first run's `folder` is `C:\verksted k4\Ärger\work`; the second is
  refused `folder_busy`. Step 3: refused `bad_files`. Step 4 (unregistered): accepted with `folder`
  `C:\verksted-k4-outside` and `config.workspace` `C:\verksted-k4-outside`, `config.files` empty —
  the `[file]` list of `C:\verksted k4` does **not** apply, because the run is in the junction's
  target. That is the documented limit without registration (k5); note it, it is not a failure.
  Step 5: refused `not_in_workspace`; nothing is stored.

### J The `\\?\` prefix and long paths

- **§7 row:** "Windows-Ordner mit Leerzeichen und Umlaut" (which folder the run is in); §7
  "Umfang von Version 1: lokale Arbeitsordner" (UNC and device paths are refused).
- **Linux stand-in:** `test_win_long_path_prefix_is_accepted_and_stored_plain`,
  `test_win_paths_outside_v1_are_refused`.
- **Steps:**
  1. Record `(Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem).LongPathsEnabled`.
  2. Run with folder `\\?\C:\verksted k4\Ärger\work`.
  3. Create a folder longer than 260 characters:
     `New-Item -ItemType Directory -Path ('\\?\C:\verksted k4\long\' + 'x'*120 + '\' + 'y'*120)`.
     Run with that folder once with and once without the `\\?\` prefix.
  4. Run with `\\localhost\C$\verksted k4\Ärger\work` and with `\\.\C:\verksted k4`.
- **Expected:** step 2: accepted, `folder` is `C:\verksted k4\Ärger\work` without the prefix.
  Step 3 with `LongPathsEnabled` 0: refused — `path_too_long` with the prefix, `folder_missing`
  without; with 1: accepted with the plain path in `folder` — then also note whether the child
  can write a file there (that is Hermes' tools, not Verksted's check). Step 4: refused
  `unc_path` and `device_path`.

### K A WSL backend given a `C:\` path

- **§7 row:** "Windows-Ordner mit Leerzeichen und Umlaut" (the file lands in the right folder),
  on a WSL backend; §6 "ein Windows-Pfad wird nicht ungeprüft dorthin weitergereicht".
- **Linux stand-in:** `test_wsl_a_windows_drive_path_is_mapped_and_checked`,
  `test_wsl_a_share_path_must_name_this_distro` (Hermes' `translate_cwd_for_wsl_backend` at
  `ad2b705` with `is_wsl` patched — no real WSL).
- **Needs** a Desktop connection to a backend inside WSL (`docs/hermes-interfaces.md` Windows
  check §2.1: a `remote` or `ssh` gateway entry), the package installed in that backend's
  `HERMES_HOME` as in §0.3, and the Desktop half enabled. Without one, record "not run". In the
  distro, record `echo $WSL_DISTRO_NAME` and `mount | grep -E 'drvfs|9p'`.
- **Steps:**
  1. Run with folder `C:\verksted k4\Ärger\work`, task *"write `wsl.txt` containing the word
     hallo"*.
  2. Start a slow run there (the terminal is the distro's shell: `sleep 60` in place of `ping`);
     while it runs, run in `c:\VERKSTED K4\ärger\work`.
  3. In the distro `mkdir ~/verksted-k6`; run with folder
     `\\wsl.localhost\<distro>\home\<user>\verksted-k6`, then with another distro name in place of
     `<distro>`.
  4. Run with `\\server\share\x`.
- **Expected:** step 1: `folder` is `/mnt/c/verksted k4/Ärger/work`; `wsl.txt` appears in the
  Windows folder. Step 2: refused `folder_busy` (if instead `folder_missing`, the drvfs mount is
  case-sensitive — record the mount options). Step 3: the first is accepted with `folder`
  `/home/<user>/verksted-k6`; the other distro name is refused `windows_path`. Step 4: refused
  `windows_path`. No path reaches `session.create` in its Windows form.

## Stop, follow-up, interruptions and results (k8, k10)

Scenarios L–S check the rows of `docs/architecture.md` §7 about Stop, lost confirmations, a
restarting backend, Rückfragen and approvals nobody answers, result outputs, and the lean profile.
Preconditions as in §0. Use `wait_seconds: 0` and ask for the tool's JSON result verbatim wherever a
`state`, `cancel` or `result_block` value is the point. Each scenario names the Linux stand-in in
`tests/e2e/test_verksted_e2e.py` / `tests/e2e/test_verksted_k11_e2e.py` (real Hermes backend `ad2b705`,
shimmed window). **None of them has been exercised on Windows.**

### L Cancel a running turn (and before any window)

- **Design:** §4/§5 step 3 "Stop" (no §7 row of its own): a stop counts only when Hermes confirms it; a closed tab is not a stop.
- **Linux stand-ins:** `test_12_cancel_of_a_running_turn_is_confirmed_by_the_receipt`;
  `test_21_cancel_before_any_desktop_window_ends_cancelled_and_nothing_reaches_hermes`.
- **Steps:**
  1. Start a slow run (*"run `ping -n 120 127.0.0.1`, then write `slow.txt`"*), `wait_seconds: 0`.
     Wait for "läuft".
  2. Parent: *"Cancel the verksted run `<handle>` and show me the result."* Watch the Verksted page:
     "wird gestoppt", then "fertig" (outcome `interrupted`).
  3. Close the child's tab instead of cancelling on a second slow run: the run keeps going.
  4. With the Desktop part switched **off** (Capabilities, Plugins), start a run; it stays
     "wartet auf Hermes Desktop". Cancel it from the parent. Switch the Desktop part on again.
- **Expected:** step 2: the tool answers `cancel: requested`, later `cancel: confirmed` with
  `outcome: interrupted`; `slow.txt` does not appear after the stop; the child answer produced after
  the stop is not the result. Step 3: no stop; a closed tab never interrupts. Step 4: `state:
  cancelled`, `cancel: not_started`; after the Desktop part is on again no session is created for
  that run and the child chat never receives the task. A cancel after the turn finished answers
  `cancel: too_late` and the result is unchanged.
- **Not exercised on Windows.**

### M Follow-up turn with send, also after the child session was closed

- **§7 row:** "GUI schließen und erneut öffnen" (a follow-up reaches the same child session; no row of its own for `send`).
- **Linux stand-ins:** `test_13_send_runs_a_follow_up_turn_in_the_same_child_session`;
  `test_24_send_after_the_child_was_orphan_closed` (idle grace shortened there).
- **Steps:**
  1. Finish a run (scenario A). Parent: *"Send the verksted run `<handle>` this follow-up: append
     the word `zwei` to `result.txt`; wait for it."*
  2. Close the child's tab and every chat that shows it, leave the Desktop window open for more
     than `sessions.orphan_end_grace_s` (default 300 s) and send a second follow-up.
- **Expected:** step 1: the same `handle` and `session_id`, `turn: 2`, one new prompt in the **same**
  child chat, no second session in the list. Step 2: the follow-up runs in the same session (Hermes
  resumes an orphan-ended session); if Hermes refuses, the tool says so as a refusal and nothing is
  retried. Record which.
- **Not exercised on Windows.**

### N Lost submit reply after the turn finished (P1)

- **§7 row:** "Zwei GUI-Fenster und verlorene Startbestätigung" (Kein automatischer Doppelstart).
- **Linux stand-ins:** `test_14_a_lost_submit_reply_after_the_turn_finished_repeats_the_identical_submit`,
  `test_20_takeover_in_create_requested_repeats_the_identical_create_and_starts_one_session`.
- **Reproducible only with luck on Windows.** Start a short run and kill the Desktop window
  (Task Manager) within a second of "wird gestartet"; reopen it after the child finished. Record "not
  run" if the timing cannot be hit.
- **Expected:** exactly one child session and one prompt in its history; the run ends "fertig" (or
  "unbekannt" with the reason shown, never a second prompt). Nothing is started twice.
- **Not exercised on Windows.**

### O Backend killed mid-turn: unknown, nothing retried

- **§7 row:** "Gateway-Abbruch während einer Aktion" (unclear outcome becomes visible `unknown`; no blind retry).
- **Linux stand-in:** `test_15_a_gateway_killed_mid_turn_leaves_the_run_unknown_and_nothing_is_retried`.
- **Steps:**
  1. Start a slow run, wait for "läuft".
  2. Kill the backend process of the throwaway home (`Stop-Process` on the Python process that
     `hermes gateway status` names) and let the Desktop start it again.
  3. Parent: *"Check the verksted run `<handle>`."*
- **Expected:** "unbekannt" (`state: unknown`, reason `admission_unknown`); the hint says nothing is
  retried and to use "Session öffnen" to discard or resend the lost prompt there. No second prompt
  appears in the child chat on its own; the slow command is not run twice.
- **Not exercised on Windows.**

### P Rückfrage with nobody attached, approval nobody answers

- **§7 row:** "Verweigerte Freigabe" (the proposed action does not run); the Rückfrage case is design §5, no row of its own.
- **Linux stand-ins:** `test_17_a_rueckfrage_asked_with_no_viewer_is_reported_unanswered`,
  `test_18_an_approval_nobody_answers_ends_blocked_and_the_command_does_not_run`.
- **Steps:**
  1. Set `approvals.timeout: 20` in the throwaway `config.yaml`, restart the gateway. Start a run
     whose task is to run the dangerous command of scenario C. Do not open the child. After 20 s
     check the run and the parent's result.
  2. Start a run whose first act is a clarify question; close the Desktop window within a second of
     "wird gestartet" and reopen it after a minute (the question was asked while nobody viewed the child).
- **Expected:** step 1: the command did not run (`denied.txt` absent), the run ends "fertig" with
  `outcome: completed`, the result carries `blocked` (`reason: timeout`) and the page shows
  "Nicht freigegeben: …". Step 2: the result carries `unanswered` with the question and the page
  shows "Rückfrage ohne Antwort: …"; the child went on with an assumption and Verksted says so.
  Neither the plugin nor the main agent answered anything.
- **Not exercised on Windows.**

### Q Two Rückfragen at once, answered in reverse order

- **§7 row:** "Zwei Sessions mit gleichzeitigen Rückfragen" (each answer reaches only its own request).
- **Linux stand-in:** `test_19_two_concurrent_rueckfragen_are_answered_in_reverse_order_each_reaching_its_own_run`.
- **Steps:** start two runs in two folders (`work`, `work2`), each asking one clarify question
  (*"What is the project called? Write it into name.txt"*). Open both children with "Session öffnen".
  Answer the second run's question first (`zwei`), then the first (`eins`).
- **Expected:** `work\name.txt` holds `eins`, `work2\name.txt` holds `zwei`; each run's result mentions
  its own answer only.
- **Not exercised on Windows.**

### R Result outputs: not_found, outside_workspace, symlink escape; no block

- **Design:** §4 step 5 (result with summary and output paths; no §7 row of its own); paths are checked on the executing backend.
- **Linux stand-ins:** `test_16_the_result_block_is_checked_against_the_workspace_root`,
  `test_22_no_result_block_is_reported_missing_although_the_file_exists`,
  `test_23_an_unclosed_result_block_is_reported_unparsable`; the Windows path rules run on `ntpath`
  in `tests/agent/test_result.py` (simulated).
- **Steps:** register `C:\verksted k4` as workspace (as scenario I step 5) and keep the junction
  `work\out` -> `C:\verksted-k4-outside` from scenario I. Run in `C:\verksted k4\Ärger\work` with the
  task *"write `notes.md`; in your closing block list `notes.md`, `ghost.md`,
  `C:\verksted-k4-outside\secret.txt`, `out\secret.txt` and `C:\Windows\win.ini`"*. Then a run with
  the task *"write `plain.md` and answer in one sentence, no block"*.
- **Expected:** `outputs` has only `notes.md` (relative path, size, time); `missing` has `ghost.md`
  `not_found` and `outside_workspace` for the secret (absolute), the junction path and `win.ini`
  (a drive-relative or UNC form: `bad_path`); the Verksted page lists "→ notes.md" and "✗ … (reason)".
  Second run: `result_block: missing`, no outputs although `plain.md` exists; the page says "kein
  Ergebnisblock". Case variants (`NOTES.MD`), 8.3 names and `\\?\` forms: record what happens.
- **Not exercised on Windows.**

### S Lean profile (MCP) and a stale Desktop half after an update

- **§7 row:** "Schlankes Profil" (Nicht benötigte MCP-Server werden nicht zusätzlich gestartet).
- **Linux stand-ins:** `test_lean_profile_starts_no_unneeded_mcp`,
  `test_fat_profile_mcp_toolset_is_dropped_but_the_server_still_runs` (an enabled server starts with the
  gateway; Verksted sends `no_mcp`, so the child is not offered it, but the process runs). The stale
  Desktop half has **no** Linux stand-in; `tests/agent/test_package.py` only checks that both manifests
  carry one version.
- **Steps (MCP):** in the throwaway `config.yaml` add `mcp_servers:` with one server that writes a line
  to a file when it starts (a `python -c` that appends to `C:\verksted-k4\spawn.txt` and then reads
  stdin) and set `enabled: false`; restart the gateway; run a task with `toolsets: [file]`. Repeat with
  `enabled: true`.
- **Expected (MCP):** disabled: `spawn.txt` is never written, the child's tool list has no MCP tool.
  Enabled: the server starts once with the gateway (one line), the run is not refused, the child is not
  offered its tools, and the run starts no second process. Whether Windows leaves a console window for
  the server: record it.
- **Steps (stale half):** install build 1, enable the Desktop part, run once. Update to build 2 as in
  §0.3 **without** deleting `desktop-plugins\verksted`, Rescan, and compare
  `(Get-FileHash "$env:HERMES_HOME\plugins\verksted\desktop\plugin.js").Hash` with the hash of
  `$env:HERMES_HOME\desktop-plugins\verksted\plugin.js`. Then repeat with the delete step.
- **Expected (stale half):** record whether the hashes differ after a plain overwrite (the Desktop keeps
  its copy while the mtime looks current) and that they are equal after the delete and Rescan; also run
  `hermes plugins doctor` and note whether it flags the stale copy. A run started by a stale half must
  not be trusted; the report names the build of each half.
- **Not exercised on Windows.**

## Not covered here

Real window unload firing the Desktop part's dispose (a run's claim is given back at once instead of
after the 180 s lease: watch whether a second window takes a run over within seconds after the first one
is closed, scenario D), Hermes Desktop restart during `send_waiting`, and everything in the Windows path
rows that `ntpath` only simulates. Those stay open.
