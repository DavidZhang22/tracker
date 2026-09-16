# Model improvement roadmap

September 16, 2026. Original research and proposed experiments. The subsequent implementation, measured results, and remaining work are recorded in [the model pipeline report](../reports/model-pipeline.md).

## Recommendation

Build on the existing record-context network to give link selection and metadata association a shared understanding of each record. First remove repeated context work and establish a trustworthy evaluation set. Then compare compact models on identical data and runtime budgets. Increasing tree count alone is not the next step.

The intended result is a model that recognizes a collection and its records, identifies which links serve each record, and associates dates, languages, and other fields with the correct record. Deterministic format decoding, supported APIs, URL safety, deduplication, and user-selected filters remain explicit contracts.

## What the evidence says

The production link classifier has two routed 120-tree branches, with depth capped at four. Only one branch evaluates an anchor: a job-table specialist or the generic fallback. The numeric JSON is roughly 210 KB. It uses up to 84 structural features; its deployed text vocabulary is empty even though the training pipeline supports text features. The separate record-context neural network has 929 parameters. Model weights are a small part of the application's memory use.

The latest dataset expansion has 18,855 anchor rows in total, but its new real-world coverage comes from only five index pages. Thousands of anchors on one template are not thousands of independent examples of generalization. Authored layouts and weakly labeled pages are useful development material, but cannot establish accuracy on unfamiliar websites.

The expanded candidate improved NASA coverage from 19 to 25 expected links and PMLR from 206 to 327. On W3C, both models recovered 1,233 expected links, but outside-scope links rose from 178 to 575. Increasing the candidate from 120 to 180 or 240 trees also lost validation recall. The candidate was correctly withheld. See the [full comparison and its limitations](../reports/generalization.md).

There is also an integration issue to investigate before comparing architectures: the candidate's standalone validation threshold is 0.45, while the complete parser comparison intentionally retains the existing 0.25 threshold and job branch. This does not invalidate the recorded regression, but it means retraining, routing, and threshold selection must be evaluated together. Do not choose a new threshold using the observed W3C results and then call W3C an unseen test.

A new offline W3C replay on the development machine took 9.37 seconds without profiling. A separate instrumented run took 22.39 seconds:

| Operation | Instrumented cumulative time |
| --- | ---: |
| CSS selection entry point | 7.52 s |
| Date association, `link_date` | 6.77 s |
| Record text/context, `text` | 4.16 s |
| Link classification including feature preparation | 3.44 s |
| Tree scoring, `score` | 1.07 s |

These times overlap: date/context functions call selectors and other helpers. They must not be summed. Profiler overhead also changes their proportions. Tree scoring accounts for about 5% of this instrumented trace, so accelerating tree arithmetic alone cannot eliminate the majority of the delay. This is one diagnostic replay, not a production latency estimate or an improvement over the earlier benchmark. The [profile report](../reports/model-roadmap-profile.json) records the capture hash, environment, and measurements.

Existing improvements already include two persistent analysis processes, native dense arithmetic for the small neural networks, compressed response queues, bounded fetch concurrency, page-local caches, and lightweight refresh recipes. The next work should extend these rather than recreate them. The previous 1 GiB stress test peaked at 453.6 MiB; it does not cover every possible page or concurrent import. See [memory measurements](MEMORY_PIPELINE.md) and [parallel analysis](PARALLEL_ANALYSIS.md).

## 1. Make context extraction cheaper

Create a bounded record representation shared by the link classifier, record model, keyword filter, and date extractor. A record contains its structural group, title and text spans, canonical URLs with roles, nearby metadata candidates, source order, and provenance. Office/PDF/text adapters should produce the same representation while retaining their format-specific evidence.

Walk the DOM once to index parent/child relationships, headings, repeated sibling patterns, links, and metadata spans. Compute reusable subtree summaries bottom-up. Cache canonical URLs, parsed date evidence, and candidate-region features per page. Store offsets or compact identifiers where practical instead of repeatedly copying ancestor text. Preserve missing values, not just a zero that could also mean an observed negative.

Start with the measured hotspots in `dates.py` and `record_context.py`: repeated heading selectors, sibling searches, descendant scans, and multiple ways of assembling the same text. Retain record-boundary checks so a faster date extractor cannot borrow a neighboring chapter's date. Relative-date caches must include the observation time; date precision and publication/modification/listing distinctions must survive.

Keep safety pruning separate from relevance prediction. Remove executable payloads only where existing structured-data extraction no longer needs them; retain hidden/collapsed content and relevant accessibility attributes. Avoid pruning a container merely because of depth or an unfamiliar class name.

Audit the first-4,000-anchor classification budget. Navigation and duplicate anchors can consume capacity before later records are considered. Deduplicate repeated evidence and allocate the bounded candidate budget across structural groups. Measure candidate-generation recall independently of classifier recall, and surface incomplete coverage when limits apply. Do not silently discard older records or raise the 4,999-entry storage limit.

Validate these changes with identical features, extracted URLs, metadata, and ordering on frozen fixtures before changing model behavior. Any parser replacement such as a faster native DOM library is a separate experiment: malformed HTML tree construction and selectors must retain equivalent behavior. Optimize the current traversals first.

## 2. Build evaluation data that reflects the actual task

Start with 100–200 reviewed collection pages from 50–80 independent domains, then expand according to errors and learning curves. These are collection targets, not a claim that this amount guarantees generalization. Cover chapters, videos, podcasts, blogs, jobs, papers, releases, events, courses, products, datasets, and mixed archives. Include multilingual text, language icons, multiple links per record, records without links, tables, cards, paired rows, nested lists, and metadata outside the immediate container. Include saved documents and pasted content with consent or public provenance.

Label collection scope, record boundaries, link roles, and the exact metadata evidence. A footer URL is not universally irrelevant, and a job's application URL can be more useful than its title URL. Distinguish primary content, alternate destination, author/category, navigation, promotion, and ambiguous cases. For language filtering, annotate which label belongs to which record. For dates, annotate type, precision, and unknown/ambiguous cases.

Split by domain and shared platform/template family, with near-duplicate detection across splits. Reserve fresh domains and later snapshots for a final test that is not consulted while iterating. Use group-aware folds inside the development data; [scikit-learn's grouped validation](https://scikit-learn.org/stable/modules/cross_validation.html) supports non-overlapping groups. Report macro results by page and domain, alongside pooled counts, so large tables cannot dominate the score.

Use data sources according to their labels:

| Source | Useful contribution | Limitation |
| --- | --- | --- |
| Existing captures and explicit user corrections | Direct examples of Trackify's task | Separate weak labels from reviewed labels; existing test pages are already observed |
| Bounded [Common Crawl](https://commoncrawl.org/get-started) HTML samples | New structures without additional requests to origin websites | Raw HTML still needs task-specific annotation; retain provenance and check reuse terms |
| [WebSRC](https://x-lance.github.io/WebSRC/) | HTML and grounded question/answer spans for studying metadata relationships | Its question-answer labels are not labels for which links Trackify should track |
| Existing CleanEval/Web2Text material | Boilerplate and DOM stress examples | Main-text extraction is a different task; not a gold link-relevance benchmark |
| Authored transformations | Wrapper insertion, class changes, sibling metadata, distractors, missing dates | Keep transformed copies in the same split and validate that the intended labels remain true |

Prioritize disagreement between models, uncertain records, and diverse failing templates for review. Limit how many nearly identical anchors each page contributes. Include hard negatives such as related recommendations that look like the main collection, plus hard positives such as an external application URL labeled only with an icon.

Explicit “wrong link,” “missing entry,” and “wrong date” corrections can become opt-in training examples. Muting, deleting, and favoriting are user preferences and must not automatically become relevance labels. Private uploads and account data remain outside public training artifacts.

## 3. Compare a small set of model candidates

Run controlled feature and architecture comparisons; do not change the dataset, decision threshold, parser, and model simultaneously.

| Candidate | What it tests | Decision |
| --- | --- | --- |
| Current routed boosted trees, calibrated and compiled | Faster execution without changing the learned decision function | First speed baseline |
| Sparse word/character n-grams plus logistic regression and structural features | Whether inexpensive learned text features improve unfamiliar labels and URL formats | First semantic baseline |
| Regularized LightGBM over shared record features | Stronger structural interactions with native inference | Compare against current trees; constrain leaves, depth, and tree count |
| Small pooled-text neural network with structural inputs | Joint use of wording, user intent, record shape, and neighboring records | Preferred research candidate after the simpler baselines |
| Small sequence model or graph network | Whether relationships between records need more than pooled group context | Defer unless error analysis demonstrates the need |
| MarkupLM/other large document models | Rich structural supervision or an offline teacher | Keep outside the live refresh path |

The sparse baseline can use Unicode-normalized words and bounded character n-grams, field identifiers, URL path segments, and query keys. Preserve numbers and language codes in separate features. Exclude hostname identity as a shortcut. Fit vocabularies on training data only, or use bounded hashing and evaluate collision effects. [Scikit-learn's feature extraction guide](https://scikit-learn.org/stable/modules/feature_extraction.html) describes these sparse representations.

For the neural candidate, begin with 16,384 token/subword buckets and 16- or 32-dimensional pooled embeddings, concatenate structural features, then use a 64-to-32-unit shared network. The embedding table alone is 1 or 2 MiB in float32. Total parameters would be roughly 0.3–0.6 million depending on the heads and sharing: a proposed budget, not a trained result. Bounded averaged embeddings provide a [fastText-style baseline](https://fasttext.cc/docs/en/supervised-tutorial.html); library defaults and large vocabularies should not determine our memory budget.

Add mean/max summaries of records sharing a template, plus local position and neighboring evidence. Pooling provides inexpensive group context; [Deep Sets](https://arxiv.org/abs/1703.06114) motivates this family of representations, but does not establish that it will improve Trackify. Avoid all-pairs attention over thousands of page nodes.

Use separate prediction heads for link role/collection membership and metadata association. Score bounded record-to-field candidates rather than generating dates or URLs. Include source heading and user criteria as context, but keep exact include/exclude and language filters deterministic. “All links” document import must continue to preserve explicit input by default.

Treat collection ranking as a helper, not a top-k output limit: several groups can be valid and the user wants every relevant record. Increase width, vocabulary, or depth only when grouped learning curves show underfitting and gains on fresh layouts. Use page-balanced training, regularization, and early stopping before increasing size. [LightGBM's tuning guidance](https://lightgbm.readthedocs.io/en/latest/Parameters-Tuning.html) describes capacity controls for its leaf-wise trees.

If a larger teacher becomes worthwhile, fine-tune or adapt it offline using public, reviewed records and train the small model to reproduce its useful decisions. [MarkupLM](https://arxiv.org/abs/2110.08518) combines text and markup for document understanding; [knowledge distillation](https://arxiv.org/abs/1503.02531) provides a teacher-to-student training approach. Teacher output remains weak supervision until reviewed, and must never label the final test set used to claim independent quality.

## 4. Calibrate decisions and make the model primary gradually

Scores are currently not calibrated probabilities. Fit a small calibration layer on group-disjoint development predictions, then select thresholds on separate development data. Start with sigmoid calibration; compare more flexible calibration only with enough independent samples. Keep the final test untouched. [Scikit-learn's calibration documentation](https://scikit-learn.org/stable/modules/calibration.html) explains the need for disjoint fitting and calibration data.

Deploy one fitted base model plus its small calibrator, rather than retaining every cross-validation model as a serving ensemble. Version thresholds with their model, preprocessing, and routing contract. Give a specialist a separate threshold only if there is adequate validation evidence. Route by structures such as application tables or document records, rather than adding hostname-specific classifiers.

Evaluate a high-confidence acceptance region, high-confidence rejection region, and an uncertain region where existing structural evidence or the preview can resolve the choice. Measure accuracy against the fraction handled automatically; abstention must not conceal missing entries in metrics. Confidence need not clutter every library row.

Promote model-led collection and link-role selection one validated input class at a time. Retire overlapping heuristic rules only after their regression tests pass under the model. Continue to decode feeds, APIs, document relationships, dates, and canonical URLs deterministically. A model cannot recover content that was never delivered or resolve a 403 by classification.

## 5. Optimize inference after selecting useful features

Batch feature rows within each existing analysis worker, initially comparing batches of 64, 128, and 256. Prepack float32 numeric arrays once, rather than repeatedly constructing dictionaries and converting each feature during tree traversal. Do not send individual DOM nodes to separate processes or wait for unrelated websites to fill a batch.

[Treelite supports importing sklearn gradient-boosted classifiers](https://treelite.readthedocs.io/en/latest/tutorials/import.html), and [TL2cgen produces compiled C prediction libraries](https://tl2cgen.readthedocs.io/en/latest/tutorials/first.html). Test exporting the actual routed model, including its intercept and float32 comparisons, from the trusted training/build environment. Check all scores within tolerance and require unchanged threshold decisions and complete parser outputs. Export support alone does not prove parity with our numeric-JSON interpreter.

Compare this with ONNX Runtime for a selected neural model. Load one session per persistent worker and initially use one native inference thread per worker; [ONNX Runtime thread settings](https://onnxruntime.ai/docs/performance/tune-performance/threading.html) need explicit tuning to avoid oversubscribing a two-CPU container. Include session startup, dependency memory, tokenization, and matrix preparation in measurements.

Test float32 first. Evaluate int8 only when the neural model is large enough for it to matter: [ONNX quantization](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html) can alter accuracy, and dynamic quantization adds work during inference. Compare static calibration and dynamic quantization on the deployment CPU, particularly near acceptance thresholds. Pruning should remove whole channels, features, or tree depth when targeting ordinary CPU kernels; merely storing zero weights does not guarantee faster execution.

Use a model artifact target below 8 MiB, with an initial incremental resident-memory target below 32 MiB per analysis worker, including the chosen inference runtime. These are experiment gates, not current measurements. The existing numeric JSON loader has a 2 MB cap and narrow validation: any larger or different artifact needs its own bounded, validated format and trusted build contract. Do not relax upload limits or accept user-supplied executable models. Training frameworks remain outside the serving image.

Revisit free-threaded Python only after these changes. The existing parser-only experiment was 13% faster than the process configuration, but also changed Python version/build and did not validate the full application stack. Dependency compatibility, memory, and lifecycle tests are prerequisites; a runtime migration is not the first generalization improvement.

## 6. Acceptance gates and rollout

Freeze the baseline before implementation. Measure source fetching, candidate generation, feature extraction, inference, metadata association, and database merge separately. Compare unprofiled repeated runs in the same two-CPU, 1 GiB image; use profiling only to explain differences. Include small feeds, nested cards, job tables, W3C-sized pages, 4,999-record imports, and concurrent refresh/import workloads.

Initial targets, to be assessed with domain-level uncertainty and adequate test coverage:

| Area | Proposed gate |
| --- | --- |
| Extraction quality | At least 98% macro precision and 97% macro recall on the new reviewed benchmark; report both by domain and content family |
| Regressions | No lost required entries on critical fixtures; investigate any family recall regression over one percentage point |
| Metadata | At least 99% correct association for emitted dates on the reviewed benchmark; date coverage at least baseline, with unknowns counted separately |
| Ordering and identity | Preserve canonical deduplication, date precision, number order, and saved read/muted/favorite state |
| Speed | Target at least 20% lower median and p95 offline analysis time on the slow-page suite, with no material small-page regression |
| Memory | Target peak total app-container memory below 750 MiB under the defined concurrent stress suite; hard cap remains 1 GiB |
| Network | Same request budgets and pacing; no child-content fetches to supply model context |

Also report unwanted links per page, completely correct pages, candidate coverage, language-filter errors, ambiguity/abstention rate, and date-order inversions. A model can meet a pooled F1 score while producing an unacceptable number of unwanted links on a few large pages.

Replay candidates on saved responses first. Follow with a bounded shadow evaluation on public/consented samples, accounting for its extra CPU and memory, then a small rollout with an immediate fallback. Version feature schemas, weights, thresholds, and parsed-result/recipe cache contracts. Keep existing items and reading progress intact during rollback; uncertain or failed scans must not erase saved content.

## Suggested implementation sequence

1. Freeze evaluation and threshold contracts; optimize measured DOM/date/context hotspots with output-parity checks.
2. Add reviewed records from independent templates and establish a fresh final test; prototype native execution of the current model.
3. Compare sparse text, regularized trees, and the small shared record network using the same splits and preprocessing ablations.
4. Calibrate the winning candidate, evaluate size/compression, and promote only after full-parser and container gates pass.
5. Add teacher distillation or richer graph/sequence context only for persistent, well-labeled error classes.

The highest-confidence next investment is shared context extraction plus better scope/role labels. The small record-aware neural model is the most promising research direction, but its promotion should depend on beating the simpler baselines rather than on being a neural network.
