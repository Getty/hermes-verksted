# Step 20: write

The research notes go in, a short draft comes out. This file is the whole contract for a session
started in this folder. Hermes does not load the workspace's `AGENTS.md` here, so everything
this step needs is below or named by path. Paths are relative to the workspace root, which is
two levels up from this folder (`../..`). Your task names the run folder `runs/<run-id>/`.

## Inputs

- The task text: the run folder `runs/<run-id>/`, plus the audience and length if the user gave
  them.
- `runs/<run-id>/10-research/notes.md` (required) and `runs/<run-id>/10-research/brief.md`.
- `references/writing-rules.md`: read it before you write anything.

If `notes.md` is missing, stop and say so: step 10 has not run for this run folder.

## Task

1. Create `runs/<run-id>/20-write/` if it does not exist.
2. Write `runs/<run-id>/20-write/draft.md` for the audience in `brief.md`, or for a general
   reader if it names none. About 300 words unless the task says otherwise.
3. Use only what `notes.md` states, and keep each statement's source as a link or a file path.
   Add no new facts. Leave out a finding you cannot use and say so in your final answer.
4. End the draft with a section "Sources" that lists every source the draft uses.

## Outputs

- `runs/<run-id>/20-write/draft.md`: the draft with its "Sources" section.
- Your final answer: one paragraph on the draft, then every file you wrote.

## Limits

- Write only inside `runs/<run-id>/20-write/`. Leave the notes, `workflow/`, `references/` and
  `.verksted.yaml` as they are.
