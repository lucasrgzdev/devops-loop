# mpod-fund

**GitHub:** https://github.com/lucasrgzdev/mpod-fund

## What this project does

An LLM-orchestrated multi-pod investment fund simulator. A central System Manager oversees autonomous investment pods, each running a specialist strategy with no mandate overlap. The system learns through a versioned prompt feedback loop, and sustained outperformance triggers automatic pod spin-offs. Phase 1 is a single-pod MVP with analyst feedback loops and full audit logging.

## Tech stack

- Python 3.11+
- Anthropic Claude SDK (all agents)
- YAML (configuration)
- pytest + mypy + ruff (quality gate)
- Custom domain models via `@dataclass` (no ORM)

## Notes

- Architecture is three-layer: System Manager (orchestrator) → Pods (strategy units) → Execution Layer (SOR, fill attribution).
- System Manager runs a 7-step cycle with 5 specialist agents: RiskMonitor, ComplianceGuard, CapitalAllocator, PerformanceEvaluator, MacroOracle.
- Prompt versioning uses semver with 30% canary traffic at launch — always update prompt versions, never mutate in place.
- Causal attribution chain is the critical invariant: analyst → prompt_version → signal → order → fill → FillAttribution. Do not break this chain.
- Phase 1 scope: single pod, analyst feedback, audit logging. Out of scope: persistence to a real database, live market data, broker/FIX connectivity, UI.
- Conforms to `general/STANDARDS.md` — run the full quality gate before adding features.
