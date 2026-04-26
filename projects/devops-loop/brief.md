# devops-loop

**GitHub:** https://github.com/lucasrgzdev/devops-loop

## What this project does

An autonomous AI-powered DevOps loop. Users manage software projects from a phone-friendly web dashboard; three AI agents (Planner, Developer, Reviewer) run a continuous scheduled loop that reads tickets from and pushes code to GitHub repositories. Designed for VPS deployment with GitHub Actions CI/CD and mobile PWA access.

## Tech stack

- Python 3.11 + FastAPI
- SQLite + SQLModel (database)
- APScheduler (agent loop scheduling)
- Anthropic Claude SDK (Planner + Developer agents)
- Google Generative AI / Gemini (Reviewer agent)
- PyGithub (GitHub integration)
- Vanilla HTML + CSS + JS frontend (PWA manifest)
- systemd (VPS process manager)
- GitHub Actions (deployment pipeline)
- ntfy.sh (push notifications)

## Notes

- Three-agent consensus model: Planner proposes, Developer implements, Reviewer validates. All three must agree before a commit is pushed.
- The git-repos version is the canonical source; no-git-repos/devops-loop is a working copy / active dev sandbox.
- Cost tracking is built-in: every API call logs token spend to the database; a spend dashboard is available in the UI.
- Deployment target is a Linux VPS (Ubuntu/Debian) managed via systemd; the GitHub Actions pipeline handles deploys.
- The UI is intentionally mobile-first and installable as a PWA ("Add to Home Screen").
- `.env` holds all secrets; never commit it. The database file is also excluded from git.
