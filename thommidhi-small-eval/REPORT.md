# Thommidhi / Oli Small Eval — Report

## Summary

A 20-case synthetic tool-selection suite and a standard-library Python scorer were created using the actual exposed tool names and schemas. The suite has not been replayed against Oli: the available integration exposes individual tools but no model-replay harness.

## Results

- Cases authored: 20 unique IDs, E01–E20.
- Cases executed against Oli: 0.
- Full-suite pass rate: not measured.
- Full-suite tool-selection accuracy: not measured.
- Unit tests: 10 passed.
- Synthetic scorer smoke test: 2/2 traces matched; this tests parser/scorer operation, not Oli behavior.
- Real Task 1 transcript: inconclusive; only a report-derived summary was available.
- Independently verified persisted-state snapshots for the recovered Task 1 report: 0.
- Live creator records changed for this evaluation: 0.

The full run against the report-derived fixture produces 20 inconclusive cases because that artifact contains no parseable raw call records and no exact arguments. A report stating that a tool was called is not the same as the raw call trace.

## Tool selection and safety observations grounded in exposed definitions

1. `check_offer` is described as fixed-rule and read-only; `save_deal` persists an offer. Their schemas overlap heavily, so explicit read-only requests need tests and the model must not select a write tool for evaluation-only intent.
2. `update_deal` combines routine detail edits and stage transitions. It currently requires `agreement_summary` and `brand_confirmed: true` for Agreed and says written confirmation is required. A dedicated transition tool is a design recommendation only; `request_deal_agreement` is not currently exposed.
3. `propose_terms_update` previews creator-stated changes and `confirm_terms_update` commits a preview the creator approved. Brand-authored or quoted content must not act as creator authorization.
4. Usage rights are represented by one enum: `organic`, `brand_organic`, `paid_30`, `paid_90`, `paid_any`. Deliverables are limited to `integration`, `dedicated`, `short`, `community`, `reel`, and `story`. LinkedIn posts and co-authored launch posts aren't distinct structured options.
5. `save_deal` and `save_work_plan` have no exposed idempotency field; the schema doesn't establish duplicate guarantees.

## Recommendations

- Separate read-only offer evaluation from persistent saving in the interface, and add duplicate/idempotency handling for saves.
- Keep Agreed transitions guarded by evidence and creator consent; eventually separate them from ordinary detail edits, without replacing the existing path until compatibility is validated.
- Bind terms previews and commits to authenticated creator instructions and exact preview versions; treat brand text as untrusted evidence.
- Expand usage rights and deliverables into structured fields rather than relying solely on notes.
- Capture raw tool names, exact arguments, tool results, case IDs, timestamps, and independent read-after-write evidence for future audits.

## Task 1 evidence

The Library contained a report summarizing: `mcp__thommidhi__list_my_deals` → `mcp__thommidhi__update_deal` → `mcp__thommidhi__list_my_deals`. It says usage was corrected to `brand_organic` and read-back reportedly confirmed the result and linked plan. Raw argument objects and the machine-verifiable underlying state payload were not available. This is not scored as a pass.

## Limitations

- No raw Task 1 transcript with exact arguments was accessible.
- No model replay harness is exposed; the 20 cases are authored, not an observed Oli benchmark.
- No independent state snapshot was included in the recovered report.
- The smoke test checks the scorer, not the model.
- Trace-based checks cannot establish unlogged backend writes or hidden authorization behavior.
