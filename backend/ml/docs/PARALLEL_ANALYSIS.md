# Parallel analysis

Measured September 13, 2026 against `69f87d4`. Production uses two reusable
Python 3.12 processes for full-page parsing, context extraction and classification.
The record and legacy link neural networks use `math.sumprod` for their dense
layers. Model weights, thresholds, tokenization and extraction rules are unchanged.

## Results

The same four complete pages were replayed in separate, network-disabled
containers limited to two CPUs and 512 MiB on the Azure VM. The workload contains
two 1,000-link nested chapter lists, the saved SimplifyJobs README, and the saved
NovelsHaven series page. Each mode has three samples; startup is measured separately.
Raw samples, interpreter versions, per-job CPU time, output hashes and memory
measurements are in [parallel-benchmark.json](../reports/parallel-benchmark.json).

| Execution | Median batch | Maximum event-loop delay | Container peak memory |
| --- | ---: | ---: | ---: |
| Previous inference, two Python 3.12 threads | 9.21 s | 381 ms | 91 MiB |
| Native inference, two Python 3.12 threads | 9.03 s | 373 ms | 90 MiB |
| Native inference, two Python 3.12 processes | 6.70 s | 23 ms | 192 MiB |
| Native inference, two free-threaded Python 3.14.7 threads | 5.83 s | 34 ms | 200 MiB |

The deployed configuration reduces batch analysis time by 27%. Pool startup adds
0.65 s once at application startup. Container peaks include all child processes
and charged file cache; they are not a worst-case memory guarantee. An additional
two-page test with 4,999 links per page completed in 9.50 s and peaked at 224 MiB.
Its CPU/wall ratio was 1.98, confirming concurrent CPU execution.

The four-page batch is uneven: the large job table outlasts the other pages.
Parallelism is between complete pages from concurrent scans; one page's DOM and
context stay together in a single worker. This avoids repeatedly serializing DOM
trees or losing neighboring/parent context. This improves library throughput;
it does not promise a proportional speedup for every individual item. Network,
host pacing and sequentially discovered pagination still contribute to refresh time.

## Model verification

`verify_native_inference.py` compares the previous implementation with native
inference over all 31,113 record-context vectors and 8,507 legacy link vectors
(training and holdout combined). It does not train or change any model.

| Check | Record-context network | Legacy link network |
| --- | ---: | ---: |
| Inference speedup | 1.92x | 2.69x |
| Acceptance/rejection changes | 0 | 0 |
| Largest absolute probability difference | 3.89e-16 | 4.44e-16 |

All 2,922 grouped context selections are identical. Full scan output hashes,
including URLs, dates, order, language, context, metadata, pagination and feeds,
match across all four execution modes. Tiny floating-point differences are
expected because [sumprod](https://docs.python.org/3/library/math.html#math.sumprod)
uses native extended-precision accumulation. These are performance improvements,
not claims of improved classification accuracy on unseen sites.

## Scheduling and safeguards

- The parent retains HTTP fetching, budgets, host pacing, caches and SQLite.
  Workers receive strings and return scan data; no database connection or DOM
  object is shared. They make no network requests.
- Admission happens before process submission, with at most two submitted jobs.
  Existing API scan and library-refresh admission limits still apply. A cancelled
  request retains its slot until the underlying job finishes.
- Processes start with `spawn`, preload model files once, and shut down with the
  application. A broken pool produces a recoverable scan error; the next request
  creates a replacement pool. Saved links remain intact and failures do not loop.
- Full scans remain enabled. There is no early stop at known entries. Existing
  complete-body parse reuse, ten-minute scan caching, request caps, pacing and
  the 4,999-link storage limit remain in effect. Content pages are not visited
  merely to obtain missing dates.
- `TRACKER_ANALYSIS_WORKERS=2` is the default. Set `1` for a smaller single-CPU
  host, or `0` to use the previous thread scheduling with native inference.
  Keep one Uvicorn application worker: each additional API process owns another
  pool. Restart the service after changing this setting.

The backend suite has 248 passing tests, including real child-process output
equivalence, worker death/recovery, bounded admission on repeated cancellation,
unchanged-page cache bypass, and application startup/shutdown. The existing
refresh cancellation and database safeguards also pass. All 53 frontend tests
pass. `verify_parallel_runtime.py` additionally exercises real HTTP in a disposable
VM library: two processes overlap, the 20-link item is committed before the
1,500-link item finishes, and an intervening library GET takes 4.4 ms.

## Free-threaded Python

The isolated 3.14.7 parser test used exactly the locked parser dependency versions.
The GIL remained disabled after imports and throughout analysis; output hashes
matched. It was 13% faster than the selected process configuration, but this
comparison also changes Python version and build, so it does not isolate the GIL's
contribution. Production remains on its tested Python 3.12 dependency lock.

The initial full locked-stack installation in the free-threaded slim image hit
a missing C++ compiler while building `greenlet==3.2.4`; this is a build failure,
not proof that the application cannot run free-threaded. A production migration
needs a full dependency/build audit and authentication, HTTP, database and lifecycle
tests. [Python's documentation](https://docs.python.org/3/howto/free-threading-python.html)
also notes that unsupported extensions can re-enable the GIL. We did not force
the GIL off for an unsupported extension or change the production runtime.

## Reproduce

From `backend`, with the project's Python 3.12 environment:

```sh
python ml/benchmark_parallel.py --mode all --repeats 3 --output data/parallel.json
python ml/verify_native_inference.py --output data/native.json
python ml/verify_parallel_runtime.py
python -m pytest -q
```

Add `--capture data/refresh-public-captures.json` when the existing public capture
is available; without it, the benchmark uses only generated chapter pages.
Captures are deliberately excluded from Git. `--links 4999` runs the larger case.
Use separate containers with the same CPU/memory limits to compare memory peaks.
The optional `Dockerfile.freethreaded` builds the parser-only experiment from a
local `catchup-app:latest` image. Use `/opt/free/bin/python` to run the same benchmark.
It is not a replacement production image and installs no training packages.
