# avante

**GitHub:** https://github.com/lucasrgzdev/avante

## What this project does

Avante is an agent-based speculative outreach platform. It runs campaigns that research prospects, produce tailored assets (email sequences, content strategies, site builds), queue them for human review, and deliver them via Gmail — all before any relationship exists with the prospect. The pipeline is orchestrated by Claude Code; a Next.js web UI handles campaign config, asset review, and knowledge-core inspection.

## Tech stack

- Runtime: Node.js (Claude Code orchestrates all pipeline stages)
- AI: Anthropic Claude API (claude-sonnet-4-5) — research, scoring, asset production
- Frontend: Next.js + React + Tailwind CSS
- Backend: Next.js API routes (filesystem bridge + SSE for live updates)
- Storage: Markdown + YAML frontmatter (no database in v1)
- Outreach: Gmail MCP

## Notes

- The knowledge core lives in `/knowledge-core/` as markdown files — this is both the database and the Obsidian-compatible vault.
- The pipeline is purely file-based: each stage reads input files and writes output files atomically (temp + rename). The web UI and pipeline communicate only through `status.md`.
- Human review gate before every delivery is intentional in v1 — do not add auto-approve logic.
- Token budget is hard-enforced per prospect and per campaign; always read from API response headers, never estimate.
- New production modules are added by creating a folder under `/modules/` — zero changes to the orchestrator.
- v1 is local-only; no multi-tenancy, no cloud deployment, no database.
