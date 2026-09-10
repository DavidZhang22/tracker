# Keyword context model

The scanner now makes two separate decisions: the existing link classifier finds
content links, then a small neural network selects the surrounding record for each
link. Keywords match the title and that record's metadata. The second decision is
needed because a language badge, location, format, or group can be outside the
anchor's div, in a different table cell, or in a following metadata row.

The record model has 27 bounded structural inputs, one hidden layer of 32 ReLU
units, and a sigmoid output: 929 parameters, 10,294 bytes of numeric JSON. Python
runs it directly; production installs no training libraries and needs no GPU,
model API, or extra service. Model loading validates dimensions, finite weights,
size, and layer count. A missing or corrupt artifact falls back to the anchor's
own text, rather than borrowing the entire page.

Candidate regions include ancestors and adjacent sibling pairs. Inputs describe
link counts and route agreement, text versus anchor length, metadata/date tags,
repeated records, semantic containers, and sibling direction. Absolute document
depth was removed after it failed on shorter layouts. Region selection is learned;
explicit accessible references and scoped group headings supply additional
evidence. Exact language attributes, labels and API translation fields take
precedence over a conflicting group heading. Interface language on `html` or
`body` is not treated as a chapter language.

Keywords are case-insensitive, Unicode-normalized literal terms or phrases.
Every comma-separated term must match. They never become regular expressions.
Limits are 300 characters, ten terms, and 80 characters per term. Examples:
`English`, `English, official`, `Texas`, or `Science, audio`. Link URLs and query
secrets are not vectorized. Context is bounded to 1,800 characters per entry.
Changing keywords affects future scans and preserves existing saved progress.

## Data and evaluation

`record-context-dataset.jsonl` has 31,113 candidate regions: 22,666 training,
4,134 validation, and 4,313 evaluation rows. Whole layout families and public
sources stay in separate splits. Authored layouts vary record count, nesting,
semantic tags, duplicate buttons, metadata position, icons, extra links, and
single-record pages. Equivalent regions containing the same record facts are
both valid labels. Public fragments from the existing Royal Road and job-table
fixtures contribute reviewed boundaries; Hacker News and GitHub release fragments
exercise separate-source evaluation. No account data or chapter bodies are used.

These are development fixtures and authored contracts, not a blind web benchmark.
The source rules and labels are inspectable in `train_record_context.py`. Earlier
iterations exposed short-list and sibling-boundary failures; these informed the
feature and layout design. The reports must not be interpreted as universal
accuracy on unseen sites.

Linear, 32-unit neural, and 40/80/120-tree models were compared. Validation selected
the neural model: 100% correct selected regions across 419 validation anchors;
candidate-region precision and recall were both 98.53%. The separate evaluation
split selected a correct region for 415 of 417 anchors (99.52%), with candidate
precision 99.54% and recall 97.73%. The optimizer reached its 300-iteration cap;
validation performance, rather than convergence alone, determined selection.
The exported model agrees with sklearn probabilities within 0.000001.

Limits remain: visually positioned elements with no useful DOM relationship,
ambiguous unwrapped labels, and unseen widget layouts can omit context. Two
complex GitHub release cases remain conservative misses in the evaluation set.
The scanner does not invent metadata when it cannot associate it confidently.

## MangaDex

MangaDex provides a JavaScript shell. Its documented public feed supplies chapter
URLs, numbers, translation language, group and publication date. Recognized
language keywords are also sent as `translatedLanguage[]` to avoid fetching
other translations. Each page contains at most 500 records; requests use the
existing cache, pacing, timeout, page budget and 4,999-link cap. Only metadata is
requested. Unavailable chapters, wrong-title records and invalid IDs are excluded;
duplicate pages stop pagination. Different translations retain separate IDs.

The supplied One Punch-Man feed reported 1,917 available records across languages
and zero available English records during development on September 10, 2026.
Counts can change. An empty language result is reported honestly and can still be
tracked for future releases. The fixture keeps one public record for each of 16
languages observed in the first page; it contains no user profiles or media.

Schema: https://api.mangadex.org/docs/static/api.yaml

## Reproduction

From `backend`:

```sh
uv sync --group ml
uv run --group ml python ml/train_record_context.py
uv run pytest -q
uv run python ml/benchmark_record_context.py
```

Training writes `ml/record-candidate/`; promotion copies only its validated
`record-context-model.json` into `app/tracker/`. Training data is excluded from the
runtime Docker image. The model ID participates in scan caching. The local
4,999-link benchmark recovered all contexts in 15.28 seconds; model loading used
about 34 KB of Python allocations. The Azure benchmark report records deployment
memory and timing under a 512 MB container limit.
