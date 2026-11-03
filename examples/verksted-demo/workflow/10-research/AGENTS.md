# Step 10: research

A question goes in, notes with sources come out. This file is the whole contract for a session
started in this folder. Hermes does not load the workspace's `AGENTS.md` here, so everything
this step needs is below or named by path. Paths are relative to the workspace root, which is
two levels up from this folder (`../..`). Your task names the run folder `runs/<run-id>/`.

## Inputs

- The task text: the question, its scope and the run folder `runs/<run-id>/`.
- Material the task names, usually in `runs/<run-id>/input/`. There may be none.
- `references/writing-rules.md`: read it before you write anything.

## Task

1. Create `runs/<run-id>/10-research/` if it does not exist.
2. Write the question and its scope, as the task states them, to `runs/<run-id>/10-research/brief.md`.
   If the scope is unclear, ask the user one question. Without an answer, write your
   assumption into `brief.md` as a line starting with "Assumption:" and go on.
3. Research the question. Start with the material the task names, then use the web if this session
   has web tools.
4. Write `runs/<run-id>/10-research/notes.md` with 5 to 15 findings, each a short statement with
   its source, and a section "Open questions" for what you could not settle.

## Outputs

- `runs/<run-id>/10-research/brief.md`: the question and its scope.
- `runs/<run-id>/10-research/notes.md`: findings with sources, and open questions.
- Your final answer: three to five sentences on what you found, then every file you wrote.

## Limits

- Write only inside `runs/<run-id>/10-research/`. Leave `workflow/`, `references/` and
  `.verksted.yaml` as they are.
- Do not write the draft. That is step 20 (`workflow/20-write/`).
