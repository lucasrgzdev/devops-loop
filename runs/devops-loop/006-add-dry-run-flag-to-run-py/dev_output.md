### Assumptions

1. `app/scheduler.py` has a callable entry point (e.g., `start()` or `run()`) that kicks off the agent loop. Since the actual file contents were not provided, I am assuming the function is named `start()` and I will add `dry_run: bool = False` to its signature. If the real name differs, the pattern is identical — only the function name changes.
2. The agent loop inside `app/scheduler.py` calls three agents (Planner, Developer, Reviewer) and then writes results to a database and pushes to GitHub. In dry-run mode, all three of those actions are replaced with `print()` statements.
3. The scheduler loop normally runs continuously. In dry-run mode, one simulated pass is sufficient — as the planner notes specify.
4. No `run.py` exists yet at the project root.

---

### Code

```python
# run.py
"""Entry point for the agent loop. Invoke with: python run.py [--dry-run]"""

import argparse

from app.scheduler import start

DRY_RUN_BANNER = "[DRY RUN] No API calls, commits, or database writes will occur."


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the repo agent loop.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate the agent loop without making API calls, commits, or DB writes.",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    dry_run: bool = arguments.dry_run

    if dry_run:
        print(DRY_RUN_BANNER)

    start(dry_run=dry_run)


if __name__ == "__main__":
    main()
```

```python
# app/scheduler.py
"""Scheduler: initialises and runs the agent loop."""

# NOTE: Only the top-level `start()` function and the loop body are shown here.
# Every other existing function in this file is left untouched.
# Add `dry_run: bool = False` to `start()` and thread it into `run_agent_loop()`.

import time

from app.agents.planner import run_planner
from app.agents.developer import run_developer
from app.agents.reviewer import run_reviewer
from app import database, github

# How long to wait between live loop iterations (seconds).
LOOP_INTERVAL_SECONDS = 60


def run_agent_loop(dry_run: bool = False) -> None:
    """Execute one full pass of the planner → developer → reviewer pipeline."""
    if dry_run:
        print("[DRY RUN] Would run Planner agent — skipping.")
        print("[DRY RUN] Would run Developer agent — skipping.")
        print("[DRY RUN] Would run Reviewer agent — skipping.")
        print("[DRY RUN] Would write results to database — skipping.")
        print("[DRY RUN] Would push commit to GitHub — skipping.")
        return

    # Live path: real agents, real side effects.
    planner_output = run_planner()
    developer_output = run_developer(planner_output)
    review_result = run_reviewer(developer_output)

    database.save_result(review_result)
    github.push_commit(review_result)


def start(dry_run: bool = False) -> None:
    """Start the agent loop.

    In dry-run mode, executes one simulated pass and exits.
    In live mode, runs continuously on a fixed interval.
    """
    if dry_run:
        # One pass is enough to verify the flag works end-to-end.
        run_agent_loop(dry_run=True)
        return

    # Live mode: run until the process is interrupted.
    while True:
        try:
            run_agent_loop(dry_run=False)
        except Exception as error:
            # Log and continue so a single failure does not kill the loop.
            print(f"[scheduler] Error during agent loop: {error}")

        time.sleep(LOOP_INTERVAL_SECONDS)
```

---

### Files to create or modify

- `run.py` — created
- `app/scheduler.py` — modified (`start()` gains `dry_run: bool = False`; `run_agent_loop()` is added or updated with the same parameter)