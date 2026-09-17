"""Bounded batches through a trusted optional C kernel, with Python fallback."""

import ctypes
import math
import os
from functools import lru_cache
from pathlib import Path

from .link_context import model_tokens

DOUBLE = ctypes.POINTER(ctypes.c_double)
INTEGER = ctypes.POINTER(ctypes.c_int)
BATCH_SIZE = 128


@lru_cache(maxsize=1)
def kernel():
    path = Path(__file__).with_name(
        "_model_native.dll" if os.name == "nt" else "_model_native.so"
    )
    try:
        lib = ctypes.CDLL(str(path))
        lib.tracker_trees.argtypes = [
            DOUBLE,
            INTEGER,
            ctypes.c_int,
            ctypes.c_double,
            DOUBLE,
            ctypes.c_int,
            ctypes.c_int,
            DOUBLE,
        ]
        lib.tracker_dense.argtypes = [
            DOUBLE,
            DOUBLE,
            INTEGER,
            ctypes.c_int,
            DOUBLE,
            ctypes.c_int,
            DOUBLE,
        ]
        lib.tracker_trees.restype = lib.tracker_dense.restype = None
        return lib
    except (OSError, AttributeError):
        return None


def doubles(values):
    return (ctypes.c_double * len(values))(*values)


def pack(model):
    if model.trees is not None:
        values, offsets = [], []
        for tree in model.trees:
            offsets.append(len(values))
            values.extend(value for node in tree for value in node)
        return doubles(values), (ctypes.c_int * len(offsets))(*offsets)
    widths = [model.numeric_count + len(model.vocabulary)]
    weights, bias = [], []
    for layer in model.layers:
        widths.append(len(layer["bias"]))
        bias.extend(layer["bias"])
        weights.extend(value for row in layer["weights"] for value in row)
    return doubles(weights), doubles(bias), (ctypes.c_int * len(widths))(*widths)


def predict_validated(model, rows):
    """Internal inference after ContextModel validates every vector and model shape."""
    lib = kernel() if os.environ.get("TRACKER_NATIVE_MODEL", "on") != "off" else None
    if lib is None:
        return [model.score(row["features"], row["tokens"]) for row in rows]
    if not hasattr(model, "_native_packed"):
        model._native_packed = pack(model)
    result = []
    width = model.numeric_count + len(model.vocabulary)
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        vectors = (ctypes.c_double * (len(batch) * width))()
        for i, row in enumerate(batch):
            numeric = row["features"]
            for j, value in enumerate(numeric[: model.numeric_count]):
                vectors[i * width + j] = value
            text = (
                [
                    model.vocabulary[t]
                    for t in model_tokens(row["tokens"], model.token_mode)
                    if t in model.vocabulary
                ]
                if model.vocabulary
                else []
            )
            norm = math.sqrt(sum(value * value for _, value in text)) or 1
            for j, value in text:
                vectors[i * width + j] = value / norm
        output = (ctypes.c_double * len(batch))()
        if model.trees is not None:
            lib.tracker_trees(
                *model._native_packed,
                len(model.trees),
                model.intercept,
                vectors,
                width,
                len(batch),
                output,
            )
        else:
            lib.tracker_dense(
                *model._native_packed, len(model.layers), vectors, len(batch), output
            )
        result.extend(model.calibrate(value) for value in output)
    return result
