# devops-loop — Claude Code Instructions

## What this is

An autonomous AI-powered DevOps loop. A phone-friendly web dashboard lets users manage software projects; three AI agents (Planner → Developer → Reviewer) run tickets through a pipeline that reads from and can push code to GitHub repositories.

---

## Directory layout

```
devops-loop/
├── agents/             ← agent prompt files (MRA.md is the master rule set)
│   ├── MRA.md          ← master rules — all other agents defer to this
│   ├── PLANNER.md
│   ├── DEV.md
│   └── REVIEWER.md
├── db/
│   └── projects.db     ← SQLite database (gitignored)
├── projects/
│   └── <name>/
│       ├── brief.md    ← project context read by the Planner
│       └── tickets/    ← ticket files named NNN-slug.md
├── repos/              ← cloned repos (git pull'd before each run)
├── runs/
│   └── <project>/<ticket-stem>/
│       ├── planner_output.md
│       ├── dev_output.md
│       ├── review_output.md
│       └── meta.json
├── scripts/
│   ├── init_db.py      ← create tables / register projects
│   ├── import_github.py ← bulk-import GitHub repos as projects
│   ├── run.py          ← execute one ticket through the full pipeline
│   └── serve.py        ← Flask web server (UI + REST API)
├── web/                ← vanilla HTML/CSS/JS frontend
│   ├── index.html
│   ├── project.html
│   └── style.css
└── feedback_log.md     ← append-only human-readable audit log
```

---

## Quick-start

```bash
# 1. Create the database
python scripts/init_db.py

# 2. Register a project (--repo-path enables git pull before each run)
python scripts/init_db.py \
  --add-project my-app \
  --github-url https://github.com/you/my-app \
  --repo-path repos/my-app \
  --description "One-line description"

# OR bulk-import from GitHub (requires GITHUB_TOKEN env var)
python scripts/import_github.py --clone

# 3. Start the web UI
python scripts/serve.py           # http://localhost:5000

# 4. Run a ticket through the pipeline
python scripts/run.py --project my-app --ticket 001
```

`ANTHROPIC_API_KEY` must be set for `run.py`.

---

## Database schema

| Table | Purpose |
|---|---|
| `projects` | registered repos (name, github_url, repo_path, description, status) |
| `tickets` | one row per ticket file, tracks status (`open` / `running` / `done`) |
| `runs` | one row per pipeline execution — stores raw dev + review output |
| `feedback` | human approve/flag notes written via the web UI |

---

## Pipeline flow (`run.py`)

1. Look up project in DB; resolve `repo_path` and do `git pull` if present.
2. Load `projects/<name>/brief.md` + the ticket file.
3. Load `agents/MRA.md` (prompt-cached), `PLANNER.md`, `DEV.md`, `REVIEWER.md`.
4. **Planner** — receives brief + ticket → produces structured plan.
5. **Developer** — receives ticket + plan → produces code.
6. **Reviewer** — receives ticket + dev output → produces verdict (`Approve` / `Needs work`).
7. Save all three outputs to `runs/<project>/<ticket-stem>/`.
8. Write `meta.json` and append a line to `feedback_log.md`.

MRA is passed as the first system block with `cache_control: ephemeral` on every call.

---

## Web API

| Method | Route | Action |
|---|---|---|
| GET | `/api/projects` | list all projects with ticket count and last run |
| GET | `/api/projects/<id>/runs` | project detail: brief, tickets, codebase tree, run history |
| POST | `/api/runs/<id>/approve` | mark run approved; body: `{"note": "..."}` |
| POST | `/api/runs/<id>/flag` | mark run flagged; body: `{"note": "..."}` |

---

## Adding a project manually

1. `python scripts/init_db.py --add-project <name> --github-url <url> [--repo-path repos/<name>]`
2. Create `projects/<name>/brief.md` — write what the project is, its tech stack, and anything agents need to know.
3. Add ticket files as `projects/<name>/tickets/NNN-slug.md`.
4. (Optional) Clone the repo: `git clone <url> repos/<name>`

---

## Ticket file format

Tickets are plain Markdown. The Planner and Developer read them verbatim — no required schema. A minimal ticket:

```markdown
## Goal
One sentence.

## Acceptance criteria
- [ ] Bullet list
```

Tickets are identified by the leading zero-padded number: `001-init-db.md` → `--ticket 001`.

---

## Agent rules (summary of `agents/MRA.md`)

- Python 3.11+. No new libraries unless the ticket calls for one.
- `pathlib.Path` everywhere — never string path concatenation.
- Functions: one job, max ~30 lines. No global mutable state.
- Constants in `ALL_CAPS` at the top of the file.
- Comments explain *why*, not *what*. No TODO comments in output.
- Errors produce a clear human-readable message — no silent swallows.

All Planner, Developer, and Reviewer agents defer to MRA.md. Update MRA.md when the rules change.

---

## Active projects

*(Keep this in sync with the DB — `python scripts/init_db.py --list`)*

- `devops-loop` — this repo; the pipeline itself
- `developer` — the parent workspace
- `mpod-fund` — trading pod fund manager
- `pks` — Python epistemic engine
- `propositional-kms` — propositional knowledge management system
- `summa` — knowledge summarisation / graph system
- `avante` — speculative promotion engine
- `claude-template` — shared `.claude/` subtree template
- `tasting-journal` — Electron/Next.js tasting notes app

---

## Safety rules

1. **Never commit `.env` or `db/projects.db`** — both are gitignored.
2. **Never push API keys** — `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, etc. are environment-only.
3. **`feedback_log.md` is append-only** — do not rewrite history; new entries go at the end.
4. **Do not modify `repos/`** contents directly — those are managed by `git pull` inside `run.py`.
5. **Update `agents/MRA.md`** whenever coding rules change; that file is the single source of truth for all agents.
