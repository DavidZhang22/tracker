"""URL-level retrieval metrics that distinguish collection recall from false alarms."""


def metrics(rows, predictions):
    grouped = {}
    for row, prediction in zip(rows, predictions, strict=True):
        key = (row["source_id"], row["url"])
        old = grouped.setdefault(key, [row["label"], False])
        old[1] |= bool(prediction)

    def score(pairs):
        tp = sum(bool(y and p) for y, p in pairs)
        fp = sum(bool(not y and p) for y, p in pairs)
        fn = sum(bool(y and not p) for y, p in pairs)
        precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
        return dict(
            tp=tp,
            fp=fp,
            fn=fn,
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(2 * precision * recall / max(precision + recall, 1e-12), 4),
        )

    result = score(list(grouped.values()))
    sources = {}
    for (source, _), pair in grouped.items():
        sources.setdefault(source, []).append(pair)
    result["by_source"] = {
        source: score(pairs) for source, pairs in sorted(sources.items())
    }
    positive_sources = [m for m in result["by_source"].values() if m["tp"] + m["fn"]]
    result["macro_f1"] = round(
        sum(m["f1"] for m in positive_sources) / max(1, len(positive_sources)), 4
    )
    negative_only = [
        pair
        for source, pairs in sources.items()
        if not any(y for y, _ in pairs)
        for pair in pairs
    ]
    result["negative_only_urls"] = len(negative_only)
    result["negative_only_fp_rate"] = round(
        sum(p for _, p in negative_only) / max(1, len(negative_only)), 4
    )
    return result
