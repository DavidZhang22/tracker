"""Train a record-boundary specialist on disclosed, family-split layout contracts."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from bs4 import BeautifulSoup
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_fscore_support
from sklearn.neural_network import MLPClassifier

from app.tracker.keywords import normalize
from app.tracker.record_context import FEATURES, RecordContext, predict, snippets


def pages():
    for family in range(36):
        split = (
            "test" if family % 9 == 8 else "validation" if family % 9 == 7 else "train"
        )
        tag = ("div", "article", "li", "tr", "section", "div")[family // 6]
        for variant in range(12):
            rows = []
            for i in range((1, 2, 3, 5, 7, 11)[variant % 6]):
                language = ("English", "Spanish", "French", "German", "Japanese")[
                    (i + variant) % 5
                ]
                keyword = ("Remote", "Onsite", "Hybrid", "Beginner", "Advanced")[
                    (i + family) % 5
                ]
                if variant >= 8:
                    keyword += (
                        " · "
                        + (
                            "Digital",
                            "Audio recording",
                            "International edition",
                            "Official community translation",
                        )[variant % 4]
                    )
                if variant in (0, 3, 7):
                    keyword = ""
                label = ("Chapter", "Episode", "Job", "Article", "Release")[
                    variant % 5
                ] + f" {i + 1}"
                anchor = f'<a data-key="{i}" href="/entries/{i}">{label}</a>'
                if family % 6 == 0:
                    content = (
                        f'{anchor}<span class="metadata">{language} {keyword}</span>'
                    )
                elif family % 6 == 1:
                    content = (
                        f"<div><div>{anchor}</div></div><div>{language} {keyword}</div>"
                    )
                elif family % 6 == 2:
                    content = f'<span><img alt="{language}" /></span><div>{anchor}</div><small>{keyword}</small>'
                elif family % 6 == 3:
                    content = f'<div title="{language}"></div><h3>{anchor}</h3><span>{keyword}</span>'
                elif family % 6 == 4:
                    content = f'<div>{language}</div><div><span>{anchor}</span><time datetime="2026-09-10">Sep 10</time></div><div>{keyword}</div>'
                else:
                    content = f'<header><h4>{anchor}</h4></header><div aria-label="{language}">{keyword}</div><a href="/groups/{i}">Group {i}</a>'
                if tag == "tr":
                    content = (
                        f"<td>{anchor}</td><td><span>{language}</span></td><td>{keyword}</td>"
                        if family % 2
                        else f'<td><div>{anchor}</div></td><td title="{language}">{keyword}</td>'
                    )
                if variant == 2:
                    content = f'<a href="/entries/{i}"><img alt="" /></a>' + content
                if variant in (3, 6):
                    content += f'<a href="/people/{i}">Uploader</a><a href="/comments/{i}">Comments</a><a href="/groups/{i}">Community</a>'
                css = (
                    "entry-card"
                    if variant % 3 == 0
                    else "grid gap-2"
                    if variant % 3 == 1
                    else ""
                )
                rows.append(f'<{tag} data-record="{i}" class="{css}">{content}</{tag}>')
            wrapper = "tbody" if tag == "tr" else "ul" if tag == "li" else "div"
            document = (
                '<html lang="en"><body><nav>English French Remote <a href="/login">Login</a></nav><main><h1>Everything</h1>'
                + ("<div>" * (variant % 4))
                + f"<{wrapper}>"
                + "".join(rows)
                + f"</{wrapper}>"
                + ("</div>" * (variant % 4))
                + "</main><footer>Japanese Hybrid</footer></body></html>"
            )
            soup = BeautifulSoup(document, "html.parser")
            yield f"layout-{family}-{variant}", split, soup

    # Alternating rows: a title and its metadata have no enclosing card.
    for family in range(12):
        split = (
            "validation" if family % 4 == 2 else "test" if family % 4 == 3 else "train"
        )
        for variant in range(12):
            tag = ("tr", "div", "li")[family % 3]
            rows = []
            for i in range((2, 3, 5, 7, 11)[variant % 5]):
                language = ("English", "Italian", "Spanish", "French", "Japanese")[
                    (i + variant) % 5
                ]
                anchor = f'<a data-key="{i}" href="/entries/{i}">Chapter {i + 1}</a>'
                inner = f"<div><span>{anchor}</span></div>" if family % 2 else anchor
                meta = (
                    language
                    if variant % 3 == 0
                    else f'<span title="{language}"></span>Group {i}'
                    if variant % 3 == 1
                    else language + f' <a href="/users/{i}">Group {i}</a>'
                )
                if tag == "tr":
                    inner, meta = (
                        "<td>" + inner + "</td>",
                        '<td class="subtext">' + meta + "</td>",
                    )
                rows.append(
                    f'<{tag} data-pair="{i}">{inner}</{tag}><{tag} data-context-for="{i}">{meta}</{tag}>'
                )
            soup = BeautifulSoup(
                '<html lang="en"><body><nav>English</nav><section>'
                + "".join(rows)
                + "</section></body></html>",
                "html.parser",
            )
            yield f"paired-{family}-{variant}", split, soup


def labeled_regions(soup, source):
    context = RecordContext(soup)
    for anchor in soup.select("a[data-key]"):
        key = anchor["data-key"]
        boundary = soup.find(attrs={"data-record": key}) or soup.find(
            attrs={"data-pair": key}
        )
        extra = soup.find(attrs={"data-context-for": key})
        expected = set(
            normalize(
                snippets(boundary) + " " + (snippets(extra) if extra else "")
            ).split()
        )
        for node, neighbor, features in context.regions(anchor):
            actual = set(
                normalize(
                    snippets(node) + " " + (snippets(neighbor) if neighbor else "")
                ).split()
            )
            label = actual == expected and node.name not in {"html", "body", "main"}
            yield {
                "source": source,
                "anchor": source + ":" + key,
                "features": features,
                "label": int(label),
            }


def captured_pages():
    # Reviewed boundaries in minimized public fixtures; entire sources stay in one split.
    fixtures = ROOT / "tests/fixtures"
    for name, selector, split in [
        ("jobs-table.html", "tbody a", "train"),
        ("royalroad.html", "tr a", "train"),
        ("hn.html", ".titleline > a", "validation"),
        ("github-releases.html", 'a[href*="/releases/tag/"]', "test"),
    ]:
        document = (fixtures / name).read_text(encoding="utf8")
        count = len(BeautifulSoup(document, "html.parser").select(selector))
        for i in range(min(count, 80)):
            soup = BeautifulSoup(document, "html.parser")
            anchor = soup.select(selector)[i]
            key = str(i)
            anchor["data-key"] = key
            boundary = (
                anchor.find_parent(class_="Box-body")
                if name == "github-releases.html"
                else anchor.find_parent("tr")
            )
            if boundary is None:
                continue
            if name == "hn.html":
                boundary["data-pair"] = key
                boundary.find_next_sibling("tr")["data-context-for"] = key
            else:
                boundary["data-record"] = key
            yield name + ":" + key, split, soup


def evaluate(rows, probabilities, threshold):
    y = [r["label"] for r in rows]
    precision, recall, f1, _ = precision_recall_fscore_support(
        y, probabilities >= threshold, average="binary", zero_division=0
    )
    groups = {}
    for row, probability in zip(rows, probabilities, strict=True):
        groups.setdefault(row["anchor"], []).append((float(probability), row["label"]))
    chosen = [max(v, key=lambda p: p[0]) for v in groups.values()]
    return {
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "record_accuracy": sum(p >= threshold and label for p, label in chosen)
        / len(chosen),
        "anchors": len(chosen),
    }


def main():
    started = time.perf_counter()
    rows = []
    for source, split, soup in [*pages(), *captured_pages()]:
        rows.extend(dict(r, split=split) for r in labeled_regions(soup, source))
    dataset = ROOT / "ml/datasets/record-context-dataset.jsonl"
    dataset.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf8",
    )
    subsets = {
        s: [r for r in rows if r["split"] == s] for s in ("train", "validation", "test")
    }
    x = {
        s: np.array([r["features"] for r in values], dtype=np.float32)
        for s, values in subsets.items()
    }
    y = {s: np.array([r["label"] for r in values]) for s, values in subsets.items()}
    candidates = [
        (
            "linear",
            LogisticRegression(
                C=3, max_iter=500, class_weight="balanced", random_state=81
            ),
        ),
        (
            "mlp32",
            MLPClassifier(
                hidden_layer_sizes=(32,),
                alpha=1,
                max_iter=300,
                solver="lbfgs",
                random_state=81,
            ),
        ),
        (
            "trees40",
            GradientBoostingClassifier(
                n_estimators=40, max_depth=3, min_samples_leaf=8, random_state=81
            ),
        ),
        (
            "trees80",
            GradientBoostingClassifier(
                n_estimators=80, max_depth=4, min_samples_leaf=8, random_state=81
            ),
        ),
        (
            "trees120",
            GradientBoostingClassifier(
                n_estimators=120, max_depth=5, min_samples_leaf=8, random_state=81
            ),
        ),
    ]
    results = []
    trained = {}
    for name, model in candidates:
        model.fit(x["train"], y["train"])
        probabilities = model.predict_proba(x["validation"])[:, 1]
        options = [
            {"threshold": t, **evaluate(subsets["validation"], probabilities, t)}
            for t in (0.5, 0.65, 0.8, 0.9)
        ]
        best = max(
            options,
            key=lambda v: (
                v["precision"] >= 0.98,
                v["record_accuracy"],
                v["f1"],
                v["threshold"],
            ),
        )
        results.append({"name": name, **best})
        trained[name] = model
        print(name, json.dumps(best), flush=True)
    # Compare all candidates; each can be exported as bounded numeric JSON.
    choice = max(
        results,
        key=lambda r: (
            r["precision"] >= 0.98,
            r["record_accuracy"],
            r["f1"],
            -next(i for i, (name, _) in enumerate(candidates) if name == r["name"]),
        ),
    )
    model = trained[choice["name"]]
    data = {
        "version": 1,
        "features": list(FEATURES),
        "threshold": choice["threshold"],
        "model_id": "record-"
        + choice["name"]
        + "-"
        + hashlib.sha256(dataset.read_bytes()).hexdigest()[:12],
    }
    if isinstance(model, GradientBoostingClassifier):
        data.update(
            intercept=float(model._raw_predict_init(x["train"][:1])[0, 0]), trees=[]
        )
        for estimator in model.estimators_[:, 0]:
            t = estimator.tree_
            data["trees"].append(
                [
                    [
                        int(t.feature[i]),
                        float(t.threshold[i]),
                        int(t.children_left[i]),
                        int(t.children_right[i]),
                        round(float(t.value[i, 0, 0] * model.learning_rate), 8),
                    ]
                    for i in range(t.node_count)
                ]
            )
    elif hasattr(model, "coefs_"):
        data["layers"] = [
            dict(weights=np.round(w.T, 7).tolist(), bias=np.round(b, 7).tolist())
            for w, b in zip(model.coefs_, model.intercepts_, strict=True)
        ]
    else:
        data["layers"] = [
            dict(weights=model.coef_.tolist(), bias=model.intercept_.tolist())
        ]
    runtime = np.array([predict(data, f) for f in x["test"]])
    export_error = float(np.max(abs(runtime - model.predict_proba(x["test"])[:, 1])))
    assert export_error < 1e-5
    output = ROOT / "ml/experiments/records"
    output.mkdir(exist_ok=True)
    artifact = output / "record-context-model.json"
    artifact.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf8")
    report = {
        "model_id": data["model_id"],
        "rows": {k: len(v) for k, v in subsets.items()},
        "origin": "Authored layout families and reviewed public fixture boundaries; no universal accuracy claim",
        "split": "Whole layout families and sources kept in separate splits. Fixed development evaluation; not a blind benchmark.",
        "candidates": results,
        "chosen": choice,
        "test": evaluate(subsets["test"], runtime, data["threshold"]),
        "export_error": export_error,
        "model_bytes": artifact.stat().st_size,
        "seconds": round(time.perf_counter() - started, 2),
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf8"
    )
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
