"""CPU-only decision runtime benchmark over local frozen HTML; never fetches URLs.

Each case runs in a fresh subprocess with no training imports or embedding cache.
Cold means process-cold, not a flushed operating-system file cache. Warm totals
cover HTML feature extraction, optional native gating, text encoding and head
inference; native parser is a separate full-parser baseline. No network latency,
service concurrency, persistent API state, or OS memory limit is simulated.
"""

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

# Set before importing NumPy/ONNX Runtime in workers. Tokenization is serial.
for _key in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_key] = "2"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TRACKER_LINK_MODEL"] = "cascade"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
WORD = re.compile(r"(?u)\b\w\w+\b")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf8")
    ).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf8"
    )


def identity(url, base):
    parsed, origin = urlsplit(url), urlsplit(base)
    if (
        parsed.hostname == origin.hostname
        and origin.scheme == "https"
        and parsed.scheme == "http"
    ):
        return parsed._replace(scheme="https").geturl()
    return url


def decision_hashes(rows, decisions):
    pairs = [
        [r["identity"], bool(chosen)] for r, chosen in zip(rows, decisions, strict=True)
    ]
    return dict(
        candidate_decisions_sha256=digest(pairs),
        chosen_urls_sha256=digest(sorted({url for url, chosen in pairs if chosen})),
    )


def row_signature(rows, *, text=True):
    fields = (
        ("url", "features", "tokens", "identity", "text")
        if text
        else ("url", "features", "tokens", "identity")
    )
    return digest([[r[key] for key in fields] for r in rows])


def verify_rows(rows, frozen, *, text=True):
    """Allow only libm-scale numeric feature roundoff across CPU platforms."""
    if len(rows) != len(frozen):
        raise ValueError("Candidate count differs")
    maximum = 0.0
    fields = (
        ("url", "tokens", "identity", "text") if text else ("url", "tokens", "identity")
    )
    for i, (actual, expected) in enumerate(zip(rows, frozen, strict=True)):
        for field in fields:
            if actual[field] != expected[field]:
                raise ValueError(f"Candidate {i} {field} differs")
        if len(actual["features"]) != len(expected["features"]):
            raise ValueError(f"Candidate {i} feature dimensions differ")
        for a, b in zip(actual["features"], expected["features"], strict=True):
            if not (math.isfinite(a) and math.isfinite(b)) or abs(a - b) > 1e-12:
                raise ValueError(f"Candidate {i} numeric features differ beyond 1e-12")
            maximum = max(maximum, abs(a - b))
    return maximum


def local_capture(source):
    path = ROOT / source["file"]
    if not path.is_file() and not source["file"].endswith(".html"):
        path = ROOT / "data/generalization" / (source["file"] + ".html")
    path = path.resolve()
    if not any(
        path.is_relative_to((ROOT / folder).resolve())
        for folder in ("data", "ml/raw", "tests/live")
    ):
        raise ValueError("Capture must be local public evaluation data")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if source.get("sha256") and source["sha256"] != sha:
        raise ValueError("Capture hash changed: " + source["id"])
    return raw.decode("utf8"), sha


class PortableHead:
    """Inference from numeric JSON, without sklearn or cached embeddings."""

    def __init__(self, payload, encoder=None):
        import numpy as np

        self.np, self.payload, self.encoder = np, payload, encoder
        self.mean = np.asarray(payload["mean"])
        self.scale = np.asarray(payload["scale"])
        self.layers = [
            (np.asarray(layer["weights"]), np.asarray(layer["bias"]))
            for layer in payload["layers"]
        ]
        self.vocabulary = payload.get("vocabulary", {})
        self.idf = np.asarray(payload.get("idf", []))

    def lexical(self, texts):
        # Matches the frozen TfidfVectorizer defaults: lowercase, Unicode word
        # tokens of length >=2, unigrams+bigrams, sublinear TF, IDF, L2 norm.
        np = self.np
        matrix = np.zeros((len(texts), len(self.vocabulary)), dtype=np.float64)
        for i, text in enumerate(texts):
            tokens = WORD.findall(text.lower())
            counts = Counter(
                tokens + [a + " " + b for a, b in zip(tokens, tokens[1:], strict=False)]
            )
            for word, count in counts.items():
                column = self.vocabulary.get(word)
                if column is not None:
                    matrix[i, column] = (1.0 + math.log(count)) * self.idf[column]
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1.0, norms)

    def score(self, rows):
        np = self.np
        if not rows:
            return np.empty(0), dict(
                unique_texts=0,
                embedding_batches=0,
                encoding_seconds=0.0,
                head_seconds=0.0,
            )
        started = time.perf_counter()
        matrix = np.asarray([r["features"] for r in rows], dtype=np.float32)
        texts = list(dict.fromkeys(r["text"] for r in rows))
        batches = 0
        if self.payload["encoder"]:
            lookup = {text: i for i, text in enumerate(texts)}
            if self.payload["encoder"] == "tfidf":
                encoded = self.lexical(texts)
            else:
                # Encoder has its own maximum of 8 sequences / 192 tokens.
                # Outer chunks keep both public calls and output buffers bounded.
                encoded = np.empty((len(texts), 384), dtype=np.float32)
                for offset in range(0, len(texts), 128):
                    chunk = texts[offset : offset + 128]
                    encoded[offset : offset + len(chunk)] = self.encoder.encode(chunk)
                    batches += math.ceil(len(chunk) / 8)
            matrix = np.hstack((matrix, encoded[[lookup[r["text"]] for r in rows]]))
        encoded_at = time.perf_counter()
        activation = (matrix - self.mean) / self.scale
        for i, (weights, bias) in enumerate(self.layers):
            activation = activation @ weights + bias
            if i + 1 < len(self.layers):
                activation = np.maximum(activation, 0)
        scores = 1 / (1 + np.exp(-np.clip(activation[:, 0], -40, 40)))
        finished = time.perf_counter()
        return scores, dict(
            unique_texts=len(texts) if self.payload["encoder"] else 0,
            embedding_batches=batches,
            encoding_seconds=encoded_at - started,
            head_seconds=finished - encoded_at,
        )


def classify_rows(rows, baseline, head, threshold, policy):
    """Route before encoding. An empty uncertainty gate makes no encoder call."""
    if policy not in ("replace", "uncertain"):
        raise ValueError("Unknown routing policy")
    if policy == "uncertain":
        decisions = [score >= 0.5 for score in baseline]
        indices = [i for i, score in enumerate(baseline) if 0.1 < score < 0.9]
    else:
        decisions = [False] * len(rows)
        indices = list(range(len(rows)))
    scores, timings = head.score([rows[i] for i in indices])
    for i, score in zip(indices, scores, strict=True):
        decisions[i] = bool(score >= threshold)
    return decisions, dict(timings, inferred_candidates=len(indices))


def native_extract(source, html):
    from bs4 import BeautifulSoup

    from app.tracker.link_context import context_candidates
    from app.tracker.link_model import MAX_CANDIDATES
    from app.tracker.urls import canonical_url_cache

    soup = BeautifulSoup(html, "html.parser")
    try:
        with canonical_url_cache():
            return [
                {k: v for k, v in row.items() if k != "anchor"}
                for row in context_candidates(
                    soup, source.get("final_url", source["url"]), limit=MAX_CANDIDATES
                )
            ]
    finally:
        soup.decompose()


def aligned_rows(rows, page, scores=None):
    base = page["source"].get("final_url", page["source"]["url"])
    ignored = set(page["ignored"])
    kept, baseline = [], []
    for i, row in enumerate(rows):
        row["identity"] = identity(row["url"], base)
        if row["identity"] not in ignored:
            kept.append(row)
            if scores is not None:
                baseline.append(scores[i])
    return kept, baseline


def runtime_page(page, html, case, baseline, head, extract_page, parse_page):
    started = time.perf_counter()
    if case["name"] == "native-parser":
        scan = parse_page(html, page["source"].get("final_url", page["source"]["url"]))[
            0
        ]
        finished = time.perf_counter()
        actual = sorted(
            {
                identity(
                    entry.url, page["source"].get("final_url", page["source"]["url"])
                )
                for entry in scan.entries
            }
            - set(page["ignored"])
        )
        return dict(
            total_seconds=finished - started,
            chosen_urls_sha256=digest(actual),
            entries=len(actual),
        )
    native = case["name"] == "native-candidates"
    rows = (
        native_extract(page["source"], html)
        if native
        else extract_page(page["source"], html)
    )
    extracted = time.perf_counter()
    raw_count = len(rows)
    gated = native or case["policy"] == "uncertain"
    base = baseline.score_many(rows) if gated else None
    baseline_at = time.perf_counter()
    rows, base = aligned_rows(rows, page, base)
    if native:
        chosen, stages = [score >= 0.5 for score in base], {}
    else:
        chosen, stages = classify_rows(
            rows, base, head, case["threshold"], case["policy"]
        )
    finished = time.perf_counter()
    # Correctness work occurs after the timed path and checks every repetition.
    try:
        feature_error = verify_rows(rows, page["rows"], text=not native)
    except ValueError as exc:
        raise ValueError(
            "Fresh extraction differs from frozen rows: " + page["id"] + ": " + str(exc)
        ) from exc
    if gated and any(
        abs(a - row["baseline"]) > 1e-12
        for a, row in zip(base, page["rows"], strict=True)
    ):
        raise ValueError("Native scores differ from frozen baseline: " + page["id"])
    return dict(
        total_seconds=finished - started,
        extraction_seconds=extracted - started,
        baseline_seconds=baseline_at - extracted,
        candidates=len(rows),
        raw_candidates=raw_count,
        feature_max_absolute_error=feature_error,
        decisions_bits="".join("1" if bool(v) else "0" for v in chosen),
        **stages,
        **decision_hashes(rows, chosen),
    )


def summarize(records):
    result = {}
    for key in sorted({k for r in records for k in r if k.endswith("_seconds")}):
        values = sorted(r[key] for r in records if key in r)

        def percentile(p, values=values):
            rank = (len(values) - 1) * p
            low = int(rank)
            return values[low] + (
                values[min(low + 1, len(values) - 1)] - values[low]
            ) * (rank - low)

        result[key] = dict(
            p50=percentile(0.5),
            p95=percentile(0.95),
            minimum=values[0],
            maximum=values[-1],
            samples=len(values),
        )
    return result


def model_files(directory, case):
    paths = []
    if case["name"].startswith("native-") or case.get("policy") == "uncertain":
        paths.append(directory / "baseline.json")
    if not case["name"].startswith("native-"):
        path = directory / (case["name"] + ".json")
        paths.append(path)
        name = read_json(path)["encoder"]
        if name and name != "tfidf":
            paths.extend(
                ROOT / "models" / name / filename
                for filename in ("model.onnx", "tokenizer.json")
            )
    return [
        dict(path=str(path), bytes=path.stat().st_size, sha256=file_sha(path))
        for path in paths
    ]


def worker(request, output):
    loaded_at = time.perf_counter()
    from unittest.mock import patch

    from semantic_decision_data import extract_page

    from app.tracker.cascade_model import CascadeModel
    from app.tracker.parser import parse_page

    case, directory = request["case"], Path(request["directory"])
    native = case["name"].startswith("native-")
    if not native:
        import numpy  # noqa: F401

        if request["encoder"] not in (None, "tfidf"):
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
    imported_at = time.perf_counter()
    baseline = (
        CascadeModel(read_json(directory / "baseline.json"))
        if native or case["policy"] == "uncertain"
        else None
    )
    head = None
    if not native:
        from app.tracker.semantic_model import Encoder

        payload = read_json(directory / (case["name"] + ".json"))
        encoder = (
            Encoder(payload["encoder"])
            if payload["encoder"] not in (None, "tfidf")
            else None
        )
        head = PortableHead(payload, encoder)
    model_at = time.perf_counter()
    pages = [read_json(path) for path in request["page_paths"]]
    captures = {p["id"]: local_capture(p["source"]) for p in pages}
    warmup, records = [], []
    # Frozen baseline replaces the model provider, with unchanged parser code.
    with patch("app.tracker.context_model.active_context_model", return_value=baseline):
        for page in pages:
            value = runtime_page(
                page,
                captures[page["id"]][0],
                case,
                baseline,
                head,
                extract_page,
                parse_page,
            )
            warmup.append(dict(page_id=page["id"], **value))
        for repeat in range(request["repeats"]):
            for page in pages:
                value = runtime_page(
                    page,
                    captures[page["id"]][0],
                    case,
                    baseline,
                    head,
                    extract_page,
                    parse_page,
                )
                records.append(dict(page_id=page["id"], repeat=repeat, **value))
    for page in pages:
        hashes = {
            r.get("candidate_decisions_sha256", r["chosen_urls_sha256"])
            for r in warmup + records
            if r["page_id"] == page["id"]
        }
        if len(hashes) != 1:
            raise ValueError("Non-deterministic decisions: " + page["id"])
    forbidden = [
        name
        for name in ("sklearn", "torch", "semantic_decision_experiment")
        if name in sys.modules
    ]
    if forbidden:
        raise RuntimeError(
            "Training dependencies contaminated worker: " + str(forbidden)
        )
    from app.tracker.native_model import kernel

    result = dict(
        native_kernel_available=kernel() is not None,
        case=case,
        dependency_import_seconds=imported_at - loaded_at,
        cold_model_load_seconds=model_at - imported_at,
        cold_import_and_model_seconds=model_at - loaded_at,
        warmup=warmup,
        raw_timings=records,
        aggregate=summarize(records),
        by_page={
            p["id"]: summarize([r for r in records if r["page_id"] == p["id"]])
            for p in pages
        },
        decision_hashes={
            r["page_id"]: {k: v for k, v in r.items() if k.endswith("sha256")}
            for r in warmup
        },
        captures_sha256={k: v[1] for k, v in captures.items()},
        training_dependencies_loaded=forbidden,
    )
    write_json(output, result)


def process_memory(pid):
    """Linux production images need no psutil just for measurement."""
    if sys.platform.startswith("linux"):
        values = {}
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in ("VmRSS", "VmHWM"):
                values[key] = int(value.split()[0]) * 1024
        return values.get("VmRSS", 0), values.get("VmHWM")
    import psutil

    try:
        memory = psutil.Process(pid).memory_info()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        raise OSError(str(exc)) from exc
    return memory.rss, getattr(memory, "peak_wset", None)


def run_case(request, interval):

    with tempfile.TemporaryDirectory(prefix="semantic-runtime-") as temporary:
        folder = Path(temporary)
        write_json(folder / "request.json", request)
        started = time.perf_counter()
        with (folder / "worker.log").open("wb") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    str(folder / "request.json"),
                    "--output",
                    str(folder / "result.json"),
                ],
                stdout=log,
                stderr=log,
            )
            samples = []
            while child.poll() is None:
                try:
                    rss, peak = process_memory(child.pid)
                    samples.append(
                        dict(
                            seconds=time.perf_counter() - started,
                            rss_bytes=rss,
                            os_peak_rss_bytes=peak,
                        )
                    )
                except (OSError, ProcessLookupError):
                    pass
                time.sleep(interval)
            elapsed = time.perf_counter() - started
        if child.returncode:
            raise RuntimeError(
                f"Worker {request['case']['name']} failed:\n"
                + (folder / "worker.log").read_text(encoding="utf8", errors="replace")
            )
        if not samples:
            raise RuntimeError("No process RSS measurements collected")
        result = read_json(folder / "result.json")
        result["process"] = dict(
            pid=child.pid,
            wall_seconds=elapsed,
            sampling_interval_seconds=interval,
            peak_sampled_rss_bytes=max(r["rss_bytes"] for r in samples),
            os_peak_rss_bytes=max(
                (r["os_peak_rss_bytes"] or 0 for r in samples), default=0
            )
            or None,
            raw_rss=samples,
        )
        return result


def choose_pages(cache, count, page_ids):
    paths = sorted(cache.glob("*.json"))
    pages = [(path, read_json(path)) for path in paths]
    pages = [
        (path, page)
        for path, page in pages
        if isinstance(page, dict) and "rows" in page and "source" in page
    ]
    if page_ids:
        by_id = {p["id"]: (path, p) for path, p in pages}
        return [by_id[name] for name in page_ids]
    # Page selection uses candidate volume only, never labels or quality scores.
    candidates = [(path, p) for path, p in pages if p.get("split") in ("test", "fresh")]
    if len(candidates) < count:
        candidates = pages
    candidates.sort(key=lambda pair: (len(pair[1]["rows"]), pair[1]["id"]))
    if len(candidates) < count:
        raise ValueError(
            f"Need at least {count} prepared pages; found {len(candidates)}"
        )
    positions = [round(i * (len(candidates) - 1) / (count - 1)) for i in range(count)]
    return [candidates[position] for position in positions]


def check_decision_parity(result, reports):
    """Require exact page decisions from the already completed offline reports."""
    if result["case"]["name"].startswith("native-"):
        return dict(
            status="native scores checked against frozen rows"
            if result["case"]["name"] == "native-candidates"
            else "parser baseline has no semantic decision vector"
        )
    expected = {}
    for report in reports:
        values = (
            report.get("models", {})
            .get(result["case"]["name"], {})
            .get("decision_hashes", {})
        )
        for page_id, value in values.items():
            if page_id in expected and expected[page_id] != value:
                raise ValueError("Conflicting offline decision hashes: " + page_id)
            expected[page_id] = value
    checks = {}
    for page_id, hashes in result["decision_hashes"].items():
        if page_id not in expected:
            raise ValueError("Offline evaluation has no decision hash for " + page_id)
        if hashes["candidate_decisions_sha256"] != expected[page_id]:
            raise ValueError(
                "Runtime decisions differ from offline evaluation: " + page_id
            )
        checks[page_id] = True
    return dict(
        status="exact candidate decision hashes match offline evaluation", pages=checks
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "ml/experiments/semantic-decision"
    )
    parser.add_argument("--cache", type=Path, default=ROOT / "data/semantic-decision")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "ml/reports/semantic-decision-runtime.json",
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        action="append",
        help="Completed offline report(s); require exact decision-hash parity for every selected head/page",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument(
        "--page-id",
        action="append",
        help="Explicit representative page, repeat at least three times",
    )
    parser.add_argument(
        "--model",
        action="append",
        help="Named head override; absent selection requires --threshold and --policy",
    )
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--policy", choices=("replace", "uncertain"))
    parser.add_argument("--rss-interval", type=float, default=0.01)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(read_json(args.worker), args.output)
        return
    if (
        args.repeats < 5
        or args.pages < 3
        or (args.page_id and len(set(args.page_id)) < 3)
    ):
        parser.error(
            "Use at least five repetitions and three distinct representative pages"
        )
    if not 0.001 <= args.rss_interval <= 0.1:
        parser.error("RSS sampling interval must be between .001 and .1 seconds")
    directory, cache = args.directory.resolve(), args.cache.resolve()
    selected = choose_pages(cache, args.pages, args.page_id)
    selection_path = directory / "selection.json"
    selection = read_json(selection_path) if selection_path.exists() else None
    finalists = {}
    if selection:
        for filename, key in (
            ("protocol.json", "protocol_sha256"),
            ("validation.json", "validation_sha256"),
        ):
            if selection[key] != file_sha(directory / filename):
                raise ValueError("Selection provenance changed: " + filename)
        finalists = {
            v["name"]: v for v in (selection["overall"], selection["semantic"])
        }
        for name, sha in selection["finalists"].items():
            if file_sha(directory / (name + ".json")) != sha:
                raise ValueError("Finalist weights changed: " + name)
    names = args.model or list(finalists)
    if not names:
        parser.error(
            "Prepare selection.json, or specify --model, --policy, and --threshold"
        )
    evaluation_reports = [read_json(path) for path in (args.evaluation or [])]
    if any(not value.get("complete") for value in evaluation_reports):
        raise ValueError(
            "Offline evaluation must complete before runtime parity is checked"
        )
    cases = [dict(name="native-candidates"), dict(name="native-parser")]
    for name in dict.fromkeys(names):
        chosen = finalists.get(name, {})
        threshold = (
            args.threshold if args.threshold is not None else chosen.get("threshold")
        )
        policy = args.policy or chosen.get("policy")
        if policy is None or threshold is None or not 0 <= threshold <= 1:
            parser.error(
                "Each head needs a frozen selection or explicit valid --threshold and --policy"
            )
        cases.append(dict(name=name, threshold=threshold, policy=policy))
    report = dict(
        version=1,
        complete=False,
        environment=dict(
            platform=platform.platform(),
            python=platform.python_version(),
            cpu=platform.processor(),
            inference_threads=2,
            tokenizer_parallelism=False,
        ),
        methodology=dict(
            network="No fetch: local captured HTML is read before timed iterations",
            cold="Fresh process per case; import and first model load reported separately; OS file cache is not flushed",
            warm="One untimed warmup per page, then fresh extraction and actual uncached encoding on every repetition",
            native_candidates="Production numeric/token feature extraction plus frozen native cascade",
            semantic_total="Semantic text/numeric extraction, neutral alignment, native uncertainty gate when used, deduplicated embeddings, and numeric head; excludes downstream parser",
            parser="Separate unchanged full parser with frozen native cascade; semantic totals do not include downstream parser",
            feature_parity="Exact candidate/text/token identity; numeric features <=1e-12 absolute difference for cross-platform libm roundoff. Decisions must match offline hashes exactly.",
            memory="Sampled total child-process RSS including imports, HTML and inputs; not a service-memory or 1 GB deployment proof",
            quantiles="Linear interpolation over raw warm samples; aggregate mixes page sizes",
            selection="Minimum, median and maximum candidate-volume evaluation pages by default, independent of labels",
        ),
        repeats=args.repeats,
        evaluation_reports_sha256={
            str(path): file_sha(path) for path in (args.evaluation or [])
        },
        selection_sha256=file_sha(selection_path) if selection else None,
        benchmark_sha256=file_sha(Path(__file__)),
        pages=[
            dict(
                id=p["id"],
                split=p.get("split"),
                site_family=p.get("site_family"),
                candidates=len(p["rows"]),
                fingerprint=p.get("fingerprint"),
                cache_sha256=file_sha(path),
            )
            for path, p in selected
        ],
        cases={},
    )
    for case in cases:
        print("benchmark", case["name"], flush=True)
        request = dict(
            case=case,
            directory=str(directory),
            page_paths=[str(path.resolve()) for path, _ in selected],
            repeats=args.repeats,
            encoder=read_json(directory / (case["name"] + ".json"))["encoder"]
            if not case["name"].startswith("native-")
            else None,
        )
        result = run_case(request, args.rss_interval)
        result["model_files"] = model_files(directory, case)
        result["model_bytes"] = sum(p["bytes"] for p in result["model_files"])
        report["cases"][case["name"]] = result
        write_json(args.output, report)
        try:
            result["offline_decision_parity"] = (
                check_decision_parity(result, evaluation_reports)
                if evaluation_reports
                else dict(
                    status="not compared; pass --evaluation to require matching offline decisions"
                )
            )
        except ValueError as exc:
            result["offline_decision_parity"] = dict(status="failed", error=str(exc))
            write_json(args.output, report)
            raise
        write_json(args.output, report)
        print(
            json.dumps(
                dict(
                    name=case["name"],
                    cold_seconds=result["cold_model_load_seconds"],
                    total_seconds=result["aggregate"]["total_seconds"],
                    peak_rss_bytes=result["process"]["peak_sampled_rss_bytes"],
                )
            ),
            flush=True,
        )
    report["complete"] = True
    write_json(args.output, report)


if __name__ == "__main__":
    main()
