# Record-context model expansion, September 2026

The production record model remains unchanged. The expanded data and two fixed
small-model comparisons did not produce a replacement that improved fresh
context recovery while preserving prior behavior. General parser fixes are
reported separately; these experiments concern the record-boundary specialist.

## Data and efficient generation

The [reviewed corpus](../datasets/context-expansion-v1.jsonl) contains 33 anchors
from 11 site families: five development, two validation, and four diagnostic.
Every anchor has reviewed required facts, forbidden neighboring facts, and its
own DOM boundary. The source hashes and acquisition limits are recorded in the
[dataset card](../datasets/context-expansion-v1-card.json).

Only three transformations are implemented: neutral title-link wrappers,
removal of CSS classes, and nested wrappers around detail elements. They run
locally on development records only. Boundary selectors and labels are carried
through the transformations and validated afterward. No word substitution,
LLM-generated facts, content-page fetching, or evaluation-family augmentation
is used. No-op variants are discarded. Families, original anchors, and variants
are weighted so extra copies cannot multiply a site's influence.

The final comparison fitted 17,203 candidate rows as 3,555 weighted unique
feature/label rows, a 79.3% reduction in fitted rows. That is a data-size
reduction, not a measured 79.3% training-speed claim. The complete four-candidate
comparison took 10.0 seconds locally. Only five real development families remain
far too few for a broad accuracy claim.

## Historical split problem and first comparison

The old authored generator produced 576 pages, including 96 exact-HTML groups
that crossed its stated train/validation/test split, involving 192 pages.
For example, `layout-4-*` equals `layout-34-*`, and `layout-5-*` equals
`layout-35-*`. Its previous 99.5% figure is a development compatibility result,
not an independent estimate of web generalization.

The [v1 comparison](../experiments/context-expansion-v1/report.json.gz) removed
affected pages from training. This removed all paired-sibling training layouts.
The validation-selected augmented 40-tree model improved the fresh diagnostic
set from 6/12 to 9/12 correct regions, but historical regression accuracy fell
from 99.5% to 54.7%. It was rejected.

The [training audit](../experiments/context-expansion-v1/training-audit.json.gz)
showed 99.5–100% correct retained training anchors, ruling out simple underfitting
as the main problem. Nested validation layouts remained 232/232 correct, while
paired-sibling layouts fell to 36/183. Four Hacker News anchors also failed.
Only 14 of 2,968 unique retained feature vectors had conflicting labels. Missing
structural coverage was the dominant measured issue.

## Corrected group comparison

[v2](../experiments/context-expansion-v2/report.json.gz) changes the split protocol
explicitly. The 576 authored pages become 420 unique HTML groups. Deterministic
hash ordering partitions each topology at approximately 70/15/15: paired layouts
54/9/9, nested layouts 246/51/51. No exact document crosses these splits; related
generator templates can still occur across them. Original real fixture sources
keep their original split. The four architectures/augmentation conditions and
threshold grid remain fixed.

Selection first required synthetic validation record accuracy within one
percentage point of the existing model, then used fresh validation leakage and
recovered records. Diagnostic sources never selected thresholds or candidates.

| Candidate | Fresh validation records | Grouped synthetic validation | Passes regression guard |
| --- | ---: | ---: | --- |
| Real additions + 32-unit neural | 3/6 | 99.10% | Yes |
| Real additions + 40-tree | 6/6 | 97.31% | No |
| Augmented additions + 32-unit neural | 3/6 | 100% | Yes |
| Augmented additions + 40-tree | 6/6 | 97.90% | No |

The selected augmented neural model matched the existing model on the regrouped
synthetic test at 310/312 (99.36%) and the source-family diagnostic set at 6/12
correct boundaries. It recovered only 3/6 fresh validation records, while the
existing model recovered all six. There is no supported accuracy improvement.
The old 417-anchor test matched at 415/417, but those anchors are explicitly
nonindependent compatibility checks after group reassignment.

For 512 uncached candidate predictions, the selected neural model took a median
19.8 ms versus 14.6 ms for the baseline in three local runs. Both artifacts are
about 10.3 KB and allocate about 31 KB when loaded. These timings cover model
inference only, not HTTP or DOM work. Exported probabilities differed from
sklearn by at most 0.000000652 across the full training matrix. Both neural fits
reached their 300-iteration cap. Candidate weights were not installed.

## Limits and next evidence

The real diagnostic baseline errors were inspected before these comparisons,
so this is a family-disjoint diagnostic set, not a blind benchmark. v1 and v2
ran while parser work was in progress; compare candidate and baseline within
each recorded protocol, rather than treating all cross-run metrics as a single
unchanged pipeline. Numeric production model weights stayed unchanged.

The next useful data is independently reviewed, real sibling-row and long-card
layouts, with an uninspected source-family final set. Further work should test
stronger relative-boundary features or a pairwise ranker with hard neighboring
negatives. Increasing model size alone would not repair missing structural
training coverage. Additional augmentation should be retained only when it
improves validation and preserves the regression guard.

## Reproduction

From `backend`, with the ML dependency group installed:

```sh
python -m ml.context_expansion.train ml/datasets/context-expansion-v1.jsonl --directory data/context-expansion/new-v1 --protocol legacy-v1
python -m ml.context_expansion.train ml/datasets/context-expansion-v1.jsonl --directory data/context-expansion/new-v2 --protocol grouped-v2
```

Use a new output directory for each run. Four candidate artifacts, protocol,
metrics, and inference measurements are stored losslessly as `.json.gz` under
`experiments/context-expansion-v1` and `experiments/context-expansion-v2`; together
they occupy 47.5 KB instead of 208.0 KB. Archived reports preserve measured
values. The helper's [README](../context_expansion/README.md) documents source
validation, evaluation metrics, and supported transformations.
