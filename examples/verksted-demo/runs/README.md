# runs/

One folder per piece of work: `runs/<run-id>/`. It holds that work's inputs, intermediate files
and outputs. Stable material lives in `references/`, not here.

- `<run-id>` is `YYYY-MM-DD-<slug>`: the day the work started, then two to four lowercase words
  joined by hyphens. Example: `runs/2026-11-03-heat-pump-noise/`.
- Each step writes into a subfolder named after its step folder: `runs/<run-id>/10-research/`,
  `runs/<run-id>/20-write/`. Material the user brings goes into `runs/<run-id>/input/`.
- A step is done for a run when the files its Outputs list exist in its subfolder.
- The files are ordinary working files. Edit `10-research/notes.md` before step 20, and the
  draft follows the edited notes.
- `<run-id>` names the piece of work. It is not the handle the `verksted` tool returns for one
  session.
