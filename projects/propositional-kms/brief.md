# propositional-kms

**GitHub:** https://github.com/lucasrgzdev/propositional-kms

## What this project does

Transforms raw text and PDFs into atomic propositions, organizing them in a typed epistemic graph with confidence propagation and explicit justification chains. The core value is answering "Why do I believe X?" by tracing support and contradiction links across the graph, with confidence updating as new information arrives.

## Tech stack

- Python 3.11+
- Anthropic Claude SDK (proposition extraction via LLM)
- NetworkX (graph backing store for the epistemic graph)
- pdfplumber (PDF ingestion)
- Voyage AI (embeddings — optional)
- scikit-learn (ML utilities)
- pytest + mypy + ruff (quality gate)

## Notes

- Pipeline: Source Loading → Chunking → Proposition Extraction (Claude) → EpistemicEngine (ingest → connect → propagate → explain).
- Core domain models: `Proposition`, `Source`, `Chunk`, `Edge`, `EpistemicGraph` (networkx-backed).
- The `EpistemicEngine` owns the learning queue and contradiction detection — keep this as the single authoritative entry point.
- `ExplanationTrace` produces justification chains; preserve this for auditability.
- Phase 1 MVP scope: text/PDF → propositions → graph. Out of scope: database persistence, UI, embeddings, LLM auto-linking.
- Conforms to `general/STANDARDS.md`. All entity IDs use `uuid.UUID`, not `str`.
- Previously named `pks` (propositional knowledge system) — the repo folder is `propositional-kms`.
