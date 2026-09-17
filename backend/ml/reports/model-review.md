# Model review — September 17, 2026

The release improves feature preparation and inference without changing the
production model weights. Retrained replacements and a correction ensemble were
evaluated and withheld after regression checks. No private library data was used.

## Weaknesses and changes

**Useful table structure was invisible to an older model branch.** The general
classifier fallback consumes the original numeric feature prefix, not the newer
table-feature tail. A properly headed `Title` column could therefore be treated
as an ordinary short link. The feature extractor now maps an unambiguous primary
column into the existing `semantic_title` feature. This is host-independent and
also supports named paper, chapter, episode, lesson and similar title columns.
It requires real `th` headers, a single primary column and a single distinct
target in that cell. Spanning/ambiguous headers, multiple targets and job tables
do not receive the override. Existing application-column handling is preserved.

On the collected Project Euler archive this recovers **50/50 problem links,
up from 31/50**. The page's failure informed this repair, so it is now a regression
example, not an untouched generalization test. This covers its captured listing
page; it does not claim to have crawled all problem pages or archive pagination.

**Structural inference processed text it never used.** Empty-vocabulary models
were still constructing token sets and sparse text features on every row. Native
batch inference also repeated the shape/range validation already performed by
`ContextModel.score_many`. Numeric-only models now skip text preparation and the
internal native entry point receives already validated vectors. Validation at
the model boundary, model-file checks and fixed-size native batches remain.
Malformed vectors are tested to fail before reaching the native entry point.

**Evaluation mixed two different tasks.** Negative-only auxiliary pages previously
contributed zero F1 even when every unwanted link was correctly rejected. Shared
metrics now report collection macro-F1 over sources with positive examples, with
a separate negative-only URL false-positive rate. URL duplicates remain grouped
within their source. Group aggregation now uses one pass instead of rescanning
the entire dataset per source. Historical reports retain their original values;
their macro-F1 must not be compared directly with the revised definition.

**More parameters did not reliably improve extraction.** The current rescue
network cannot veto a confidently accepted unwanted link. We trained 80-, 120-
and 180-tree alternatives and a 64/32-unit neural network using source/class
balancing and deduplication. The neural replacement reached its 450-iteration
limit and did not meet quality expectations. Standalone replacements traded away
too much recall or precision. An 80-tree correction stage recovered 92 additional
expected links on 40 development pages, without losing existing expected links
or adding outside-scope URLs there. On 15 held-out regression pages, however, it
added six outside-scope URLs and recovered no additional expected URLs. **It is
not enabled in production.** The experimental wrapper lives only in offline ML
tools, not in the app runtime.

## Data and evaluation

Collected one public listing from each of 12 configured sources using the existing
safe fetcher and request limits. Ten succeeded; two HTTP-rejected sites were not
bypassed. Eight usable linked inventories contributed **4,039 labeled anchor
rows**: 2,503 training, 709 validation and 827 test. These include long conference
archives, plain paragraph indexes, article references, release cards, mixed media
cards and problem tables. NumPy's largely unlinked announcements and CERN's
script-populated listing were retained as acquisition findings and excluded from
link-classifier labels.

Scopes were inspected from saved HTML before model predictions. Labels are
URL-level weak annotations, not independent human gold. Whole public hosts stay
in separate partitions; training and validation include legacy authored examples
as development data. The combined training run used 14,218 distinct rows. The new
training pages cover only two hosts, and the long ACL archive supplies many rows:
source balancing is essential. The corpus is still mainly English/technical and
does not establish broad multilingual performance. See the
[dataset card](../datasets/review-dataset-card.json) and
[source manifest](../datasets/review-sources.json).

Final production replay used the same 56 saved public HTML pages before and after
the fixes. **55 complete inventories matched exactly**, including titles, dates,
context, order and provenance. The remaining page gained the 19 expected problem
links, losing none and adding no unwanted URLs. Total records were 11,498 before
and 11,517 after. Existing acquisition/annotation limitations in these fixtures
remain; this is a regression comparison, not a universal accuracy percentage.

## Performance and checks

Three-run medians on 25,550 frozen feature rows, Windows development machine:

| Measurement | Before | After |
| --- | ---: | ---: |
| Native cascade inference | 0.996 s | 0.631 s |
| Python reference inference | 2.992 s | 2.930 s |
| Native/Python changed decisions | 0 | 0 |
| Maximum probability difference | 2.22e-16 | 2.22e-16 |

Native scoring is **36.6% faster** in this benchmark. This measures inference,
not complete refresh or network time. Full-parser time was essentially unchanged:
the local 56-page sums of per-page medians were 45.69 s and 45.19 s.

On Azure, two sequential, network-disabled containers used the same seven-page
workload, three repetitions, **1 GiB RAM and 2 CPU limits**. The sums of parser
medians were 18.30 s and 18.26 s; container peak memory was 107.2 and 108.4 MiB.
This isolated parser measurement excludes the live API's sentence encoder,
database traffic and separate browser worker. No runtime dependency or model
size increase was introduced. Discovery version `listing-semantics-v8` invalidates
old learned extraction decisions while retaining HTTP cooldowns.

**882 backend tests passed**, including native/Python parity, invalid vector
rejection, title-column extraction, ambiguous tables, job-table preservation and
the revised evaluation metrics. Ruff passed. The tested image was deployed with
a pre-deployment backup and rollback health checks.

Raw reports: [inference before](model-review-inference-before.json),
[inference after](model-review-inference-after.json),
[full replay before](model-review-before.json),
[full replay after](model-review-after.json),
[VM before](model-review-vm-before.json), [VM after](model-review-vm-after.json),
[candidate development](model-review-candidate-development.json),
[candidate holdout](model-review-holdout.json).

## Remaining model gaps

- Media type detection still uses bounded evidence rules. Its previous 47-profile
  corpus has 23 blogs, only one or two examples of several other classes, and no
  jobs/music examples. A larger neural model would not fix that imbalance. The
  next useful data is independently labeled music, job, course, novel and comic
  collections, plus contrasting *news about* those media.
- The 929-parameter record-boundary model relies heavily on authored layouts.
  Its high fixture scores do not establish accuracy on real multilingual badges
  or visually positioned metadata. Those need record-level boundary labels,
  rather than more URL-only labels.
- The MiniLM search/description encoder uses a bounded 192-token input and its
  existing evaluation has only 34 queries. Long descriptions and multilingual
  queries remain under-tested. Its weights were not changed based on this
  unrelated link dataset; source-grounded excerpts and lexical fallback remain.
- Unavailable JavaScript content is an acquisition problem: a classifier cannot
  recover records absent from its input. The source collector records this
  separately instead of labeling an empty shell as a successful negative page.

These choices follow grouped evaluation and leakage prevention described in
[scikit-learn's cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html)
and [common pitfalls](https://scikit-learn.org/stable/common_pitfalls.html).

## Reproduction

From `backend`, with the `ml` dependency group installed:

```sh
python ml/collect_review_sources.py --fetch  # opt-in public index requests
python ml/build_review_dataset.py           # saved HTML only
python ml/review_models.py --extra ml/datasets/review-dataset.jsonl
python ml/evaluate_review.py --candidate ml/experiments/review/cascade.json --output data/candidate-replay.json --split all
python ml/verify_model_review.py --output data/production-replay.json
python ml/verify_model_review.py --inference-only --output data/inference.json
```

Frozen feature rows reproduce training without fetching. Historical full-page
results require matching captures and code: the candidate evaluation preceded
the table-title fix. `evaluate_review.py` and `verify_model_review.py` accept
`--app-root` for the baseline app from commit `99dc9aa`. Recollecting today's
websites or rebuilding features with newer code is a new evaluation, not an exact
reproduction of the stored results. Training exports research artifacts only.
