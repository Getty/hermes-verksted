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

## Not covered here

Slim profiles and MCP (§7 "Schlankes Profil"), two simultaneous Rückfragen (k8), a gateway kill
mid-action (k8), WSL path spaces (k6), Stop (`cancel`, not in this build). Those rows stay open.
