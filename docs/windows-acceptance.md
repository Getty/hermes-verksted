# Windows acceptance scenarios for Verksted step 1 (k4)

Manual scenarios for the maintainer (or a Claude Code session on the Windows box). They are the
real acceptance of card k4 and of the Windows rows of `docs/architecture.md` §7 that the step-1
prototype (k3) touches. Nothing here has been run on Windows. Each scenario names the Linux
stand-in test that mirrors it (`tests/e2e/test_verksted_e2e.py`, run with
`.dev/venv/bin/python -m pytest tests/e2e -v`); a green stand-in says nothing about drive letters,
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
3. **Install the package** (copy, not symlink, on Windows): `verksted\` from the repo to
   `C:\verksted-k4\home\plugins\verksted\`. Do not run `hermes plugins enable` (it re-resolves the
   whole dependency environment into the home); edit `C:\verksted-k4\home\config.yaml` instead:
   ```yaml
   plugins:
     enabled: [verksted]
   approvals:
     mode: manual      # a dangerous command must prompt (scenario B)
     timeout: 300
   tools:
     tool_search:
       enabled: "off"  # optional: without it the main agent reaches `verksted` only via
                       # tool_search -> tool_call (plugin tools are deferred at ad2b705)
   ```
   Keep the `_config_version` Hermes wrote. Restart the gateway (`hermes gateway restart --detach`
   with `HERMES_HOME` set) so the agent half and `dashboard/plugin_api.py` load.
4. **Enable the Desktop half**: Capabilities, Plugins, "Verksted" on (it is opt-in, per window
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
- **Expected:** the parent gets a structured result (`state: finished`, `outcome: completed`, the
  child's last answer); `result.txt` is in `Ärger\work` and nowhere else; the child session is
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
  must not show "Freigabe erforderlich" for ever.

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

## Not covered here

Slim profiles and MCP (§7 "Schlankes Profil"), two simultaneous Rückfragen (k8), a gateway kill
mid-action (k8), Stop (`cancel`, not in this build). Those rows stay open.
