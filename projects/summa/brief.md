# summa

**GitHub:** https://github.com/lucasrgzdev/summa

## What this project does

Client vault management library for reading, writing, and analyzing Obsidian-style context vaults. Provides pipelines for ingesting external data (Gmail, Google Calendar), a retrieval layer (embedding + vector store), and a graph layer for business maps. Designed as an importable library used by client-facing agents — not a standalone app.

## Tech stack

- Python 3.11+
- python-frontmatter (Obsidian markdown + YAML front-matter parsing)
- PyYAML (config and serialization)
- Optional extras:
  - `[ai]`: Anthropic SDK, Voyage AI, ChromaDB
  - `[google]`: Google API client, Google Auth
  - `[local]`: sentence-transformers

## Notes

- This is a library — no `main.py` entry point. Install via pip and import in agent projects.
- Always use `python-frontmatter` for YAML front-matter parsing; never parse it manually.
- Key subsystems: `vault` (file I/O), `graph` (BusinessMap), `pipelines` (Gmail/Calendar/chunker/health), `retrieval` (embeddings + vector store), `db` (persistence), `night_shift` (scheduled jobs).
- Token budget tracking is built-in across sessions — use it consistently in all pipelines.
- Exceptions follow the `SummaError` base hierarchy defined in `exceptions.py`.
- Conforms to `general/STANDARDS.md`. Run the full quality gate before adding features.
