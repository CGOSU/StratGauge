# First Codex prompt

Paste the following into Codex from the repository root:

```text
Read AGENTS.md, PLANS.md, and every document referenced under the Required reading section of AGENTS.md. Do not write implementation code yet.

Then inspect the current repository and produce a concrete Phase 0 implementation plan for StratGauge. The plan must preserve these constraints: A-share daily-close workflow, AKShare primary and BaoStock fallback, manual trading only, point-in-time correctness, versioned strategies and recommendations, conservative next-session backtesting, and no LLM-generated market values.

For each step, identify the files and public interfaces involved, tests required, acceptance criteria, and verification commands. Call out unresolved decisions instead of silently choosing them. Keep Phase 0 small enough to finish and verify before adding machine learning or the AI assistant.
```

After reviewing the plan, the recommended implementation prompt is:

```text
Implement only the approved Phase 0 plan. Work in small coherent checkpoints. Run the relevant checks after each checkpoint, preserve the constraints in AGENTS.md, and update PLANS.md plus affected specifications when implementation details change. Do not add automated order execution, minute data, model training, news analysis, or unrelated infrastructure.
```

