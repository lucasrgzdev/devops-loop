## Planning Notes

**Assumption 1:** There is no `run.py` in the codebase structure provided. The closest runnable entry point is `app/main.py` and `app/scheduler.py`. I will assume the ticket means to create a `run.py` at the project root that serves as the main entry point for the agent loop, and add a `--dry-run` flag to it. If `run.py` already exists outside the listed structure, the approach below still applies.

**Assumption 2:** "Dry run" means the scheduler and agents are initialized but no API calls are made, no GitHub commits are pushed, and no database writes occur — the loop logic prints what it *would* do instead.

**Assumption 3:** The ticket has no acceptance criteria filled in. I will derive reasonable criteria from the goal and project context.

**Conflict with MRA:** The project brief lists FastAPI as the stack, but MRA says "No frameworks except what the project already uses." FastAPI is already in use, so no conflict. However, `run.py` should be a plain Python script with no new framework dependencies.

---

### Goal

Create a `run.py` entry point at the project root that accepts a `--dry-run` flag, which runs the agent loop without making any real API calls, GitHub commits, or database writes.

---

### Approach

1. Create `run.py` at the project root (sibling to `requirements.txt`).
2. Use Python's built-in `argparse` to define one flag: `--dry-run` (boolean, default `False`). No external libraries.
3. Parse arguments and store the result in a local variable `dry_run: bool`.
4. If `dry_run` is `True`, print a clear banner: `"[DRY RUN] No API calls, commits, or database writes will occur."` before anything else executes.
5. Pass `dry_run` into the scheduler startup call. The scheduler lives in `app/scheduler.py` — inspect what its startup function signature looks like; if it does not already accept a `dry_run` argument, add one with a default of `False` so existing callers are unaffected.
6. Inside `app/scheduler.py`, thread `dry_run` through to the agent loop function. When `dry_run` is `True`, replace the live agent calls (Planner, Developer, Reviewer) with `print()` statements that describe what would happen, then return without writing to the database or calling GitHub.
7. Guard `run.py` with `if __name__ == "__main__":` as required by MRA.
8. Add a one-line docstring to `run.py` explaining its purpose.

---

### Inputs and outputs

**Inputs:**
- Command-line argument: `--dry-run` (flag, no value required)
- Existing project and ticket data in the SQLite database (read-only in dry-run mode)

**Outputs:**
- Normal mode: starts the live scheduler loop (existing behaviour, unchanged)
- Dry-run mode: prints a description of each agent action to stdout instead of executing it; exits cleanly after one simulated loop iteration or runs continuously with printed output — printing is sufficient, the loop does not need to iterate indefinitely in dry-run mode (one pass is enough to verify the flag works)

---

### Edge cases to handle

- `--dry-run` passed without any other arguments — must work cleanly, no crash
- `--dry-run` not passed — existing behaviour must be completely unaffected
- The scheduler's startup function signature changes break `run.py` — the `dry_run` parameter must have a default value of `False` everywhere it is added so callers without it still work
- Unknown arguments passed on the command line — `argparse` handles this by default with a clear error; no extra handling needed
- `run.py` invoked as a module (`python -m run`) rather than a script — the `if __name__ == "__main__":` guard handles this correctly; document in the docstring that the intended invocation is `python run.py`

---

### Definition of done

- [ ] `run.py` exists at the project root and runs without errors via `python run.py`
- [ ] `python run.py --dry-run` prints the dry-run banner and does not make any API calls, GitHub commits, or database writes
- [ ] `python run.py` (no flag) starts the scheduler exactly as it did before this change — no behaviour regression
- [ ] `python run.py --help` shows a usage message that includes `--dry-run` with a plain-English description
- [ ] `app/scheduler.py` accepts `dry_run: bool = False` without breaking any existing import or call site
- [ ] All new function signatures include type hints
- [ ] No new external libraries are introduced
- [ ] `run.py` has the `if __name__ == "__main__":` guard