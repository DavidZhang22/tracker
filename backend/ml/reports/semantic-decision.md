# CPU contextual-link generalization experiment

Recorded September 22, 2026. **Testing is complete. No candidate qualifies for deployment, and production models are unchanged.** A small contextual decision model fits the CPU memory budget, but the tested versions trade away relevant links and add substantial work on large catalogs.

This tests the small encoder plus decision-head approach discussed for Jev-style models. It does not evaluate commercial Jev or reproduce an open Jev checkpoint. No GPU, hosted inference service, new production dependency, or live account data was used.

## Protocol

Five candidates use the same captured pages and current 84 structural features: a numeric 64x32 neural control, TF-IDF plus linear classifier, MiniLM-L6 plus linear classifier, MiniLM-L6 plus a 64x32 neural head, and MiniLM-L3 plus a 64x32 neural head. Both MiniLM encoders are frozen, locally verified INT8 ONNX assets. Only the small classifier heads are fitted.

- **60 training families / 60 pages**, 6,652 sampled URL examples. At most 96 URLs per class per page; equal family/class weight. One label-independent context representative per URL keeps repeated image/title anchors from dominating training.
- **25 validation families / 26 pages**, 6,507 candidate rows. All candidate rows are evaluated, without training sampling. Thresholds and replacement/uncertainty routing are selected only here.
- **21 previously disclosed regression families / 21 pages**. These are diagnostic regression data, not a new blind test.
- **8 new families / 8 pages**, 937 required URLs and 52 neutral alternatives. Source scopes and hashes were frozen independently before model outputs. This is the only fresh external holdout.

Total: **114 distinct website families and 115 pages**, including training and validation. This is not a claim of complete support for 114 websites. Family assignments are disjoint. Labels are assistant-reviewed source scopes, not independently adjudicated human gold. Pretraining overlap of the public MiniLM encoder is unknown; website separation applies to our task-specific fitting and selection.

The fresh set covers 99% Invisible, Longreads, Smitten Kitchen, the Walters Art Museum, Wireshark, MetaCPAN, No Starch Press and TVmaze. Collection used 23 requests and 2.71 MB across twelve attempted families, at most three requests per family, respecting the existing robots and pacing checks. Individual content pages were not opened. Four unusable/access-limited families remain recorded as exclusions, including an OpenStax JavaScript shell; these were not counted as classifier coverage.

Inputs contain bounded visible link text, URL path, local record text, nearby heading and page title. Hostnames and query values are omitted from vectorization. Scope selectors, expected URLs and labels never enter model text. The normal encoder token cap is 192. Context extraction caches shared record text; inference deduplicates identical inputs. The experiment does not add scraping requests.

## Accuracy

The predeclared replacement gate requires overall validation precision and recall within two percentage points of the deployed baseline, and each validation cohort's mean family F1 within .02. None of the fifty model/threshold/routing combinations passes. The strongest exploratory finalist by validation mean family F1 is **MiniLM-L6 + 64x32 neural**, threshold .7, applied only where the deployed decision score is strictly between .1 and .9. Scores from the existing cascade are threshold-normalized decisions, not calibrated probabilities.

| Validation candidate, best setting by mean family F1 | Precision | Recall | Mean family F1 |
| --- | ---: | ---: | ---: |
| Deployed cascade | 91.81% | 86.80% | .8401 |
| Numeric neural control | 83.52% | 66.67% | .7466 |
| TF-IDF + linear | 92.45% | 71.72% | .8012 |
| MiniLM-L6 + linear | 79.31% | 67.56% | .7807 |
| MiniLM-L6 + neural | 94.21% | 68.41% | .8198 |
| MiniLM-L3 + neural | 80.92% | 69.26% | .8009 |

These are unique-URL decisions before downstream parser rules, with missing expected candidates retained in recall. We then froze the finalist and replayed its decisions through the unchanged full parser.

| Full-parser offline replay | Correct / intended, deployed → finalist | Unwanted, deployed → finalist | Recall, deployed → finalist | Mean site F1, deployed → finalist |
| --- | ---: | ---: | ---: | ---: |
| Fresh eight-site holdout | 915/937 → 915/937 | 3 → 3 | 97.65% → 97.65% | .9821 → .9821 |
| Known 21-site regression | 3,420/3,669 → 3,290/3,669 | 1,901 → 1,632 | 93.21% → 89.67% | .8535 → .8184 |

The fresh full-parser result hides a decision-stage regression: the contextual finalist accepts only 765 intended URLs versus 915 for the deployed classifier. Existing table/card/list rules recover many rejected entries, so neither final recall nor final precision improves. All 937 expected fresh URLs exist among candidates, isolating these losses from fetch failures and candidate availability.

There are useful but unsafe signals. On IANA, candidate-stage unwanted URLs fall from 1,181 to 141, but downstream rules leave 1,208 unwanted URLs in final output versus 1,277 previously. On GNU, final unwanted URLs fall from 235 to 38, while correct entries fall from 154 to 42. Gutenberg loses 18 of its 25 intended entries. Filtering more aggressively is not an acceptable replacement for retrieving the right records.

## CPU runtime and memory

The Azure VM ran a separate container from the deployed image with **2 CPUs, 1 GiB, no network, no live database mounts, a read-only root filesystem and the deployed native C kernel**. Each case ran in a fresh subprocess, with one warmup and five measured runs per page. Timed semantic analysis performs actual page-local uncertainty gating and uncached encoding, not cached vector lookup.

| Page | Native candidate analysis, median / p95 | Contextual candidate analysis, median / p95 |
| --- | ---: | ---: |
| Walters, 99 candidates | .067 / .092 s | .135 / .156 s |
| Wireshark, 448 candidates | .245 / .276 s | .908 / .939 s |
| No Starch, 869 candidates | .657 / .674 s | 6.484 / 6.582 s |

Peak sampled **child-process RSS** was 91.2 MiB for native candidate analysis and 267.6 MiB for the contextual path. Contextual imports plus cold model loading took .934 s. A single contextual worker completed within the imposed memory cap; this does not certify total application memory with two workers, concurrent requests, browser activity or user state.

On No Starch, 688 distinct uncertain text inputs require encoding, which accounts for 5.69 s of the 6.48 s median. Its separate baseline full-parser median is 3.17 s. Candidate-analysis timings exclude downstream parsing and all network latency; they must not be presented as live refresh timings.

## Runtime equivalence failure

The strict runtime decision-hash gate **failed**, and the benchmark intentionally retains a nonzero exit and `complete: false`. All three timing cases finished and their measurements were saved, but the finalist did not validate as equivalent to cached-vector evaluation.

A controlled replay isolates the problem to batch composition: Windows page-local gated inference and Linux page-local gated inference make identical decisions, while the original globally batched embedding cache differs on **23 of 1,416 candidate rows** across the three pages (one Wireshark row and 22 No Starch rows). The INT8 encoder plus learned head is batch-sensitive in this setup. The full-parser accuracy table above is explicitly the original cached-vector replay; it is not a claim of runtime equivalence.

The feature checker permits only 1e-12 absolute numeric roundoff for platform math libraries, after observing a 5.6e-17 difference in a logarithmic structural feature. URLs, lexical tokens, semantic text and candidate decisions remain subject to exact checks. The semantic mismatch is not waived by this tolerance.

## Decision and next work

Keep the deployed classifier. The experiment establishes CPU feasibility, not a worthwhile replacement or a general Jev-model accuracy claim.

The next useful work is training on hard, same-record negatives and primary-link roles, then measuring whether parser rules respect those decisions. Book catalogs, reference lists and metadata links need targeted labels. For a semantic model, training, validation and runtime must use a consistent batching contract before any promotion. A record-level contextual model could then analyze shared regions once rather than encoding hundreds of nearly identical link contexts.

No production parser, model bundle, server configuration or dependencies changed. The focused tests passed **103 tests**; lint passed. The failed model quality and runtime-equivalence gates are experimental results, not passing deployment checks.

## Evidence and reproduction

[Protocol](../experiments/semantic-decision/protocol.json), [split inventory](../experiments/semantic-decision/data-summary.json), [validation grid](../experiments/semantic-decision/validation.json), [frozen selection](../experiments/semantic-decision/selection.json), [fresh holdout](semantic-decision-holdout.json), [known regression](semantic-decision-regression.json), [Linux runtime including failed parity](semantic-decision-runtime-linux.json), [batch-composition audit](semantic-decision-batch-parity.json), [collection provenance](semantic-decision-holdout-provenance.json), and [outcome/hashes](semantic-decision-gates.json).

Run from the repository root with backend development and ML dependencies installed. Original ignored public HTML captures and checksum-verified MiniLM-L3/L6 assets are required; fetching a later page does not reproduce frozen annotations. Unselected heads and intermediate embeddings are reproducible local artifacts; the finalist and frozen baseline are retained.

```sh
python backend/ml/semantic_decision_experiment.py fit
python backend/ml/semantic_decision_experiment.py evaluate --manifest backend/ml/datasets/semantic-decision-holdout-sources.json --output backend/ml/reports/semantic-decision-holdout.json
python backend/ml/semantic_decision_experiment.py evaluate --output backend/ml/reports/semantic-decision-regression.json
python backend/ml/benchmark_semantic_decisions.py --repeats 5 --page-id breadth-semantic-holdout-walters --page-id breadth-semantic-holdout-wireshark --page-id breadth-semantic-holdout-nostarch --evaluation backend/ml/reports/semantic-decision-holdout.json
python backend/ml/audit_semantic_batches.py
```

The benchmark requires the matching native kernel for the stated native comparison. The measured Linux run used the existing deployment image and its compiled kernel. Container constraints are imposed externally, not by the Python benchmark. The last benchmark command is expected to fail its semantic parity gate for this frozen candidate.
