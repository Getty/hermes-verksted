---
name: verksted
description: "Folder tasks and workflow steps in own sessions: run, track."
author: Torsten Raudssus
metadata:
  hermes:
    tags: [Workspace, Workflow, Sessions, Folders]
---

# Verksted Skill

The `verksted` tool hands one bounded task to a separate, regular Hermes session (the child)
that works in a workspace folder, and brings its result back to this chat. The child has its
own chat in Hermes Desktop, and the user answers its questions and approvals there. Your part:
pick the folder and step, write the task, start it, collect the result and report it. You never
do the child's work in its place, and you never answer for the user.

## When to Use

- The user wants work done in a folder, a workspace or a workflow step ("run the research step
  in my report workspace", "let Verksted draft it").
- A folder the user points to has a `.verksted.yaml` or `workflow/NN-name/AGENTS.md`, which
  makes it a Verksted workspace.
- Not for quick work you can do in this chat, and never to answer a child's question or approval.

## Prerequisites

- The Verksted plugin is enabled for this profile, and its Desktop part is switched on in Hermes
  Desktop (**Capabilities → Plugins**). A Hermes Desktop window connected to this profile starts
  the child session. Without one, a run waits (`state: "waiting_for_gui"`), and
  `desktop_last_poll_s` tells whether the Desktop part has ever checked in for this profile.
- The folder exists on the machine Hermes runs on, and you pass its absolute path as that machine
  sees it: a drive path such as `C:\Users\ana\Reports` on Windows, the Linux path for a Hermes in WSL.

## How to Run

Hermes usually loads plugin tools on demand.

- If `verksted` is in your tool list, call it directly with the arguments under Quick Reference.
- If it is not, look it up once with `tool_search`, then call it through `tool_call` with one
  Verksted call per `tool_call`. `tool_describe` with `names: ["verksted"]` shows its schema.

```json
{"queries": ["verksted"]}
```

```json
{"calls": [{"name": "verksted", "arguments": {"action": "status", "handle": "<handle>"}}]}
```

- If `tool_search` does not find it, Verksted is not enabled here. Tell the user, and do not
  act out a run in this chat.

## Quick Reference

Start a run. It returns a `handle` and waits up to `wait_seconds` (default 60, max 240):

```json
{"action": "run", "folder": "/absolute/path/to/workspace/workflow/10-research", "task": "<self-contained task>", "wait_seconds": 60}
```

Collect the result. This waits until the run ends or needs the user (default 120, max 240):

```json
{"action": "wait", "handle": "<handle>", "wait_seconds": 240}
```

Check without waiting:

```json
{"action": "status", "handle": "<handle>"}
```

Give a finished run a follow-up task. It runs in the same child session, with the same folder
and settings, and the child keeps its chat history:

```json
{"action": "send", "handle": "<handle>", "task": "<self-contained follow-up task>", "wait_seconds": 60}
```

Ask to stop a run, only when the user asks for it:

```json
{"action": "cancel", "handle": "<handle>"}
```

`files` (optional, for `run` and `send`) lists up to 50 files inside `folder`, relative to it,
for the child to look at first. Name every other input in the task text by path.

## Procedure

1. **Find the workspace.** Start at the folder the user means and go up through its parent folders.
   The workspace root is the topmost folder that has `.verksted.yaml` or a `workflow/` directory.
   A step folder often has a `.verksted.yaml` of its own; it is not the root. Read the root's
   `AGENTS.md` and the `AGENTS.md` of each step `workflow/NN-name/`, which states the step's
   Inputs, Task and Outputs. A plain folder without any of these is fine for a one-off task.
2. **Pick the step.** Steps run in number order. Each piece of work has its own run folder,
   `runs/<run-id>/`, under the workspace root, with one subfolder per step
   (`runs/<run-id>/10-research/`). The next step is the first one whose Outputs are missing from
   that run folder while its Inputs exist. New work gets a new `<run-id>` (follow `runs/README.md`
   if the workspace has one). Continuing work reuses the existing run folder. If it is unclear
   which run the user means, ask. A folder name alone starts nothing: run a step only when the
   user asked for it or agreed to your proposal.
3. **Write the task.** The child knows nothing of this chat: it gets its folder's `AGENTS.md`
   (loaded by Hermes) and your task. So the task names the absolute run folder, the user's
   goal and constraints in their own words, the input files by path, and the step's Outputs.
   Verksted appends the instructions for a closing result block (a summary and the output paths)
   to every task, so do not ask for a summary or a file list yourself. Leave out chat history,
   secrets and anything that reads as an approval. Keep the task under 32 KB and put long material
   into a file in the workspace.
4. **Start it.** Call `action: "run"` with `folder` set to the step folder
   (`<workspace>/workflow/10-research`), or to the workspace root for a task outside the steps.
   Make one `run` per task, and never repeat a `run` just to make sure.
5. **Follow it** with the table below. A run keeps going after your call returns
   (`still_running: true`): call `wait` again with the same handle. After about three waits with
   no change, stop and tell the user the run is still going (include the handle). Collect it later
   with `status` or `wait`. On `wait_end: "interrupted"`, stop waiting; the run keeps going.
6. **Report.** On `state: "finished"`, give the user the `summary` and the files in `outputs`
   (their `relative` paths). When the run finished, Verksted checked that each of them exists inside
   the workspace, not who wrote it or what it says. So say that the child lists the file, never that
   it wrote it, and read a file with `read_file` before you say anything about its content. A path
   in `missing` is no result: name it as missing when the step promised it. If the step's Outputs
   promise a file that is in neither list, say so. Then offer the next step, and start it only when
   the user agrees, or when the user asked for several steps in a row. In that case check each
   step's Outputs before you start the next.
7. **Follow up or stop, only when the user asks.** `send` gives a finished run more work: a
   correction, the next part, or the user's own answer to a question in `unanswered`. It works on a
   run in `state: "finished"` or `state: "cancelled"` whose child session exists, keeps the handle,
   counts up `turn`, and is followed like a `run`. Write the follow-up task as self-contained as the
   first. Never put an approval into it; a command in `blocked` is the user's to decide in the child
   session. `cancel` answers with a `cancel` field:
   - `cancel: "not_started"`: the task never reached Hermes; nothing ran in this turn.
   - `cancel: "requested"`: Hermes has not confirmed the stop yet, and the run may still finish.
     `wait` with the handle.
   - `cancel: "confirmed"`: Hermes stopped the turn. Report its result as partial.
   - `cancel: "too_late"`: the turn had already finished. Report the result as usual.
   - `cancel: "unknown"`: as `state: "unknown"` below.

   Closing a Desktop tab or window stops nothing.

| You see | It means | You do |
|---|---|---|
| `state: "finished"` | The child's turn ended. `summary` and `outputs` come from its closing block, checked by Verksted; `result` is its final answer | Report it (step 6). If the run did not end with `outcome: "completed"`, say so and treat the result as partial. With `result_truncated: true`, Verksted kept less than the child reported; the rest is in the child session |
| `result_block: "missing"` or `result_block: "unparsable"` | The child ended without a usable result block: no `summary`, no checked `outputs` | Report `result` and say that the child named no checked files. Do not take file names from `result`: look for the step's Outputs with `read_file` or `search_files`, and name only what you found |
| `unanswered` or `blocked` in a finished run | The child asked the user a question that got no answer (nobody had its session open), or a command that needed approval did not run (denied or not approved in time) | Tell the user what it asked or wanted to run; its result may rest on an assumption. The user answers in the child session (**Session öffnen**), or gives you the answer to pass on with `send`. Never approve a command yourself |
| `waiting_for: "clarify"`, `label: "Rückfrage"` | The child has a question for the user (it is in `prompts`) | Tell the user the question, and that they answer it in Hermes Desktop: **Verksted** in the sidebar, then **Session öffnen**. Do not answer it yourself, even if the user gives you the answer here. Then `wait` again |
| `waiting_for: "approval"`, `label: "Freigabe erforderlich"` | The child wants to do something that needs the user's approval | The user decides in the child session through **Session öffnen**. Never write "approved" or "genehmigt" anywhere in its place. An approval not given in time counts as denied. Then `wait` |
| `state: "waiting_for_gui"`, `state: "send_waiting"` | Stored (a follow-up task for `send_waiting`), but no Hermes Desktop window with the Verksted part has started it yet. `desktop_last_poll_s` is how many seconds ago such a window last checked in for this profile | With `desktop_last_poll_s: null` none ever has: ask the user to switch on the Verksted part in Hermes Desktop (**Capabilities → Plugins**) and open this profile there. Over 60: ask them to open Hermes Desktop on this profile. 60 or less: a window is there; ask nothing. Then `wait`. Do not start or send it again |
| `state: "create_requested"`, `state: "session_created"`, `state: "toolsets_checked"`, `state: "submit_requested"`, `state: "running"` | The run is starting or running | `wait` again |
| `cancel_requested: true`, `label: "wird gestoppt"` | A stop was requested; Hermes has not confirmed it yet | `wait`. The run may still finish; report what `wait` returns |
| `state: "cancelled"` | Stopped before its task reached Hermes; nothing ran in this turn | Tell the user. Start nothing on your own |
| `stale: true` | No Desktop window is following the run right now. `state` is the last known one ("veraltet"); the run has not stopped | Tell the user. Use `status` or `wait` once Hermes Desktop is open |
| `state: "refused"` | Refused before the task reached the child; `reason` and `detail` say why, for example tools beyond what `.verksted.yaml` allows | Explain it to the user. Do not edit `.verksted.yaml` or retry with wider settings on your own; that is the user's decision |
| `state: "unknown"` | Verksted cannot tell whether the task ran, for example after the gateway stopped mid-turn | Say so. Do not run or send it again. The user checks the child session through **Session öffnen** and decides there |
| `ok: false` | The call itself was refused; `error.code` and `error.message` say why | Fix what the message names (for example a relative `folder`), or tell the user |

Codes that need a specific response:

- `error.code: "folder_busy"`: another run is active in this folder. If `error.handle` is there,
  that run is yours: `wait` on it.
- `error.code: "too_many_runs"`: too many active runs in this profile. Collect one first.
- `error.code: "run_active"`: `send` on a run that is still going. `wait` for it first.
- `error.code: "not_sendable"`: the run has no child session to continue (for example, it was
  refused). Start a new `run` if the user wants one.
- `error.code: "outcome_unknown"`: the run's last turn has an unknown outcome. The user resolves it
  in the child session first.
- `error.code: "recursion_refused"`: you are a Verksted child session yourself. Do the work here
  or report back.
- `error.code: "not_in_workspace"`, `error.code: "toolsets_widen"`, `error.code: "toolsets_required"`,
  `error.code: "profile_not_allowed"`, `error.code: "profile_missing"`, `error.code: "bad_config"`,
  `error.code: "bad_settings"`: the `.verksted.yaml` files above the folder or Verksted's plugin
  settings do not allow this run. Tell the user what `error.message` says. Do not change them
  yourself, and do not pick another folder to get around the refusal; that is the user's decision.
- `error.code: "not_found"`: the handle does not belong to this chat. Use the handle that a `run`
  in this conversation returned.

## Pitfalls

- Only `status` and `wait` tell you what happened. Report no result, file or decision they did not
  return. "Accepted" and "running" do not mean done.
- The child's questions and approvals belong to the user, in the child's own chat. Nothing you
  write reaches them, and a new run is no way to pass an answer along.
- No run from a folder name alone, no chaining beyond what the user asked for, no new run or `send`
  after `unknown` unless the user asks for one.
- A `send` is a new turn for a finished run. It answers no question or approval of a running child.
- The result belongs to this chat. If the user comes back later, use the handle from this
  conversation.
- Use the Desktop labels exactly as they are written ("Session öffnen", "Rückfrage", "Freigabe
  erforderlich", "veraltet"), and answer the user in their own language.

## Verification

- After `finished`: every file the step's Outputs promise is in `outputs` or you found it yourself,
  no path from `missing` appears as a result, and your report says only what `summary`, `result`
  and the files you read say.
- After `cancel`: you reported a stop only on `cancel: "confirmed"` or `cancel: "not_started"`.
