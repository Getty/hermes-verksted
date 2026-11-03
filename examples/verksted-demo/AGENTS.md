# Verksted demo workspace

A two-step writing workflow: research a question, then write a short draft from the notes.
Each step is a folder under `workflow/` with its own `AGENTS.md`, the contract of that step
(Inputs, Task, Outputs). A session started in a step folder gets that step's `AGENTS.md` and,
outside a git repository, nothing from this file, so every step names the shared files it needs.

## Layout

| Path | Contents |
|---|---|
| `.verksted.yaml` | Toolsets for sessions in this workspace; the ceiling for every step |
| `references/writing-rules.md` | Stable rules that every step follows |
| `workflow/10-research/` | Step 10: a question in, notes with sources out |
| `workflow/20-write/` | Step 20: the notes in, a draft out |
| `runs/<run-id>/` | One folder per piece of work, one subfolder per step; naming in `runs/README.md` |

## Rules for every session here

- Read `references/writing-rules.md` before you write anything.
- Write only inside the run folder `runs/<run-id>/` that your task names. Leave `workflow/`,
  `references/` and `.verksted.yaml` as they are.
- Files are Markdown in UTF-8.
- Finish with a short summary that lists every file you wrote, by path from the workspace root.
