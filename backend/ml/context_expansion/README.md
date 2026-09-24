# Record-context expansion

These tools evaluate whether a tracked link receives its own record details. They
measure the extraction boundary separately from the full parser, so a rejected
link cannot masquerade as a context-model failure.

## Reviewed data

`../datasets/context-expansion-v1.jsonl` contains minimized public DOM records,
source URL/capture hash, family, frozen split, and explicit anchor annotations:
`selector`, `href`, `title`, `required`, `forbidden`, `boundary_selector`, and
optional `neighbor_selector`. Required facts measure completeness. Forbidden
facts from neighboring records measure leakage. Empty required facts are valid
negative controls for links that have no descriptions.

Keep an entire site family in one split. The loader rejects duplicate IDs, hosts
or families spanning splits, exact normalized cross-split document copies,
ambiguous selectors, changed targets, anchors outside their annotated boundaries,
missing required source facts, and forbidden facts inside the declared boundary.
The 400 KB fixture and 80-anchor limits bound offline work. This is a diagnostic corpus,
not a claim of broad web accuracy; its heldout baseline errors were inspected.

Run from `backend`:

```sh
python -m ml.context_expansion.evaluate ml/datasets/context-expansion-v1.jsonl --output data/context-expansion/replay.json
python -m ml.context_expansion.train ml/datasets/context-expansion-v1.jsonl --directory data/context-expansion/comparison-new
```

`evaluate --model candidate.json` compares a numeric record model. Full-parser
metrics still use the installed application model, as the report explicitly
states. The evaluation timer includes label matching and candidate coverage;
it must not be reported as production scan latency.

## Efficient expansion

Three deterministic transformations operate only on development records:
neutral wrappers around a title link, removal of CSS classes, and extra wrappers
around adjacent detail elements. They preserve text, URLs, original anchor
identity, and reviewed boundaries. Labels are carried through explicit DOM
markers, never guessed by another model. No-op variants are removed. Validation
and heldout sources are never augmented.

Training balances each source family, then each original anchor, then its
variants and candidates. Repeated feature/label rows are combined by summing
sample weights. This reduces fitted rows without letting larger pages or extra
variants dominate. Tree minimum leaf sizes apply to the resulting unique rows;
this is a distinct model-fitting protocol, not an equivalence claim.

The historical authored corpus contained 96 exact document groups across
training and evaluation splits. Protocol `legacy-v1` excludes affected training
pages; this also removes all paired-sibling examples and exposes a coverage gap.
The default `grouped-v2` instead deduplicates all 576 authored pages into 420 HTML
groups and deterministically partitions those groups within paired/nested
strata. Captured real fixture sources keep their original split. Groups share
no exact documents; related generator templates can still span splits. The old
417-anchor test is only a compatibility check after reassignment.

The comparison freezes two candidate architectures (32-unit neural and 40-tree)
and four thresholds before fitting, then applies a synthetic validation regression
guard of one percentage point and selects using real validation leakage, correct
records, and synthetic validation accuracy. It compares reviewed-real additions
against the same additions with synthetic DOM variants. Both retain
the deduplicated authored foundation. Numeric export must match sklearn
probabilities to less than 0.00001. Reports include model JSON/allocated size and
uncached inference timing; no candidate is promoted automatically.

More data is best added as a few reviewed records from a genuinely new layout
family, including a plausible neighboring record to expose leakage. Capture the
listing once, retain its hash, and derive all transformations locally. Do not
fetch individual content pages merely to create training examples. Preserve a
new uninspected source-family set for the next final evaluation.

Recorded comparisons and rejection reasons: [model report](../reports/context-expansion-models.md).
