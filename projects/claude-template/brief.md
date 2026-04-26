# claude-template

**GitHub:** https://github.com/lucasrgzdev/claude-template

## What this project does

A reusable git subtree template for Python projects integrating with Claude Code. Provides a standardized `.claude/` directory with settings, agent definitions, skills, reusable commands, and CLAUDE.md/IMPLEMENTATION_GUIDE.md templates. Other repos pull it in via `git subtree` to bootstrap consistent agent workflows.

## Tech stack

- Pure configuration and Markdown — no runtime code
- Intended for Python 3.11+ target projects
- Quality gate: ruff, mypy, pytest
- Claude Code commands (`.claude/commands/`) and skills (`.claude/skills/`)

## Notes

- This is a template repo, not a standalone application. Do not add application logic here.
- CLAUDE.md must stay under 150 lines; detailed context goes in IMPLEMENTATION_GUIDE.md.
- Changes here propagate to consumer repos via `git subtree pull` — keep backwards-compatible.
- The commands directory contains reusable Claude Code slash-commands: `implement`, `review`, `update-guide`, `new-model`.
- The agents directory defines isolated agent contexts (planner, reviewer) with their own scoped instructions.
