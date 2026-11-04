# MEMORY

## Recovery steps need independent try/except
In live-cutover scripts, wrap each independent recovery action (Caddy restore, app/image rollback) in its own try/except. A raising first step otherwise skips the remaining recovery, defeating the "always roll back" guarantee.

## Bash tool is restricted in this project session
The `bash` tool is deny-by-default here; only `grep`, `find`, `cat`, and `git diff` patterns are allowed. Use the native read/glob/grep tools instead of shell commands for file inspection.

## UCL release cutover is phase-gated
`/tmp/opencode/ucl_release_cutover.py` (+ `ucl_release_prepare.py`) gates on a 0600 local state file (`/tmp/opencode/ucl-release-state.json`); phases stage-config → snapshot → cutover → publish-metadata, each asserting the prior phase. State fields used as security IDs are re-validated in `state()`.
