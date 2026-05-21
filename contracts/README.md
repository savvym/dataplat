# contracts/

One subdirectory per sprint: `contracts/S<NNN>-<feature-id>/`.

Each sprint directory contains, in order of creation:

- `proposed.md` — implementer's plan: what to build, files touched, how to verify
- `feedback.md` — reviewer Mode A feedback (APPROVED or numbered changes)
- (iterate until APPROVED, overwriting `proposed.md` and `feedback.md` each round)
- `agreed.md` — frozen contract once Mode A passes
- `review-final.md` — reviewer Mode B output after implementation

The contract is the binding spec for one sprint. The implementer must not exceed `agreed.md` scope without surfacing to the human.
