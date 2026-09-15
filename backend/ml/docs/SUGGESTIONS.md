# Local suggestions

Suggestions recommend source pages to review and add. They never create items automatically or fetch recommended URLs. The existing add flow still validates the source, previews links, and enforces the eight-second addition interval.

## Candidate evidence

Normal HTML scans retain up to 20 observed source URLs: sibling series/fiction/comic collections, explicitly related channels/repositories, and named blogroll sites. The title, a bounded nearby description, and the originating library item are saved. Chapter URLs, navigation, social buttons, private addresses, credentials, and unrelated external links are excluded. Embedded-series HTML shares the same extraction step. API-only sources and pages without suitable related links may provide no suggestions; arbitrary recommendations are never invented to fill the page.

Existing libraries can use **Update suggestions** to backfill from HTTP pages already in the shared response cache. Only URLs belonging to that account's active library are considered. Each call processes at most eight changed HTML pages and 8 MB, skipping individual pages over 2 MB. A saved cache timestamp prevents repeated parsing. This operation uses the existing bounded work admission and account quota. It makes zero network requests. Ordinary source scans retain their original pacing, caching, and request budgets.

## Ranking model

A content-based TF-IDF profile suits a small personal library without enough users or feedback for collaborative filtering. Text uses Unicode NFKC normalization, case folding, percent decoding, stop-word removal, and bounded word/bigram tokens. Smoothed inverse document frequency is fitted locally on the account's library and observed candidates. The vocabulary is capped at 4,096 terms; vectors are sparse and L2-normalized. This follows the standard [TF-IDF text representation](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction), implemented here with the Python standard library.

Favorites and reading progress increase a tracked item's contribution to the profile. Dismissed recommendations form a negative profile and are excluded directly. The candidate score combines cosine similarity, the originating source's favorite/read signals, and similarity to dismissed candidates. A greedy diversity adjustment reduces repeated hosts and near-identical candidates. Similarity penalties are updated incrementally rather than recalculated against the whole selected set. Scores are ranking heuristics, not calibrated relevance probabilities, and are not shown as percentages.

Candidates already tracked, including ignored sources and Trash, are excluded. Only active, nonignored items can provide recommendation provenance. Duplicate URLs share feedback across originating sources. Dismissals and provenance remain private to the account and persist across refreshes and restarts. Trash does not get scanned or backfilled.

Storage is capped at 1,000 candidates per account, including dismissals, with at most 20 current observations per source. A page returns up to 40 ranked candidates. There is no external ML service, GPU, embedding download, or added production dependency. This is lexical personalization; synonyms, subtleties of genre, and sparse titles remain limitations. The link-extraction classifiers remain separate.

## Validation

`tests/test_suggestions.py` covers collection URL shapes, blogroll context, image labels, provenance, unsafe/noisy links, duplicates, favorite-driven ordering, persistent feedback, known/ignored/trashed exclusions, account isolation, cache-only backfill, and storage caps. These are authored contracts, not a claim of universal relevance accuracy. Source extraction also runs through the existing parser regression suite.

Run the offline benchmark with `python ml/benchmark_suggestions.py` from `backend`. It constructs 1,000 suggestions from 50 fixture sources, ranks 40, reports elapsed time and peak ranking allocations, and makes no web requests or changes to personal libraries.

The September 10 local run ranked those 1,000 candidates in 0.42 seconds and used 5.6 MiB of peak ranking allocations. Timing is measured without memory tracing; allocations are measured in a separate traced pass. These figures cover ranking this fixture, not the whole server or HTML parsing. A saved NovelsHaven page yielded 10 observed related series with clean titles and genre context, using no additional fetches.
