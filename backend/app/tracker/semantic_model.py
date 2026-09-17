"""One bounded CPU encoder per API process; model files are immutable local data."""

import hashlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from threading import Lock

DIMENSIONS = 384
MODEL_NAME = "minilm-l6"
MODEL_VERSION = "minilm-l6-int8-b941bf19-v1"
MAX_BATCH = 8
MAX_TOKENS = 192
LOAD_LOCK = Lock()


class EncoderUnavailable(RuntimeError):
    pass


class Encoder:
    def __init__(self, name=MODEL_NAME, root=None):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        ort.set_default_logger_severity(3)
        ort.disable_telemetry_events()
        manifest = json.loads(
            Path(__file__).with_name("semantic_assets.json").read_text()
        )[name]
        root = Path(root or Path(__file__).resolve().parents[2] / "models" / name)
        model = root / "model.onnx"
        if hashlib.sha256(model.read_bytes()).hexdigest() != manifest["sha256"]:
            raise ValueError("Encoder checksum mismatch")
        data = (root / "tokenizer.json").read_bytes()
        if (
            hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            != manifest["tokenizer_git_blob"]
        ):
            raise ValueError("Tokenizer checksum mismatch")
        self.np = np
        self.name = name
        self.pooling = manifest.get("pooling", "mean")
        self.query_prefix = manifest.get("query_prefix", "")
        self.tokenizer = Tokenizer.from_str(data.decode())
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(model), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.inputs = {value.name for value in self.session.get_inputs()}
        self.lock = Lock()
        self.failed = False

    def encode(self, texts, *, query=False):
        if len(texts) > 256:
            raise ValueError("Encoder batch exceeds its limit")
        if self.failed:
            raise EncoderUnavailable("Encoder unavailable")
        try:
            return self._encode(texts, query=query)
        except Exception as exc:
            self.failed = True
            logging.getLogger(__name__).warning(
                "Semantic inference unavailable; using text search."
            )
            raise EncoderUnavailable("Encoder unavailable") from exc

    def _encode(self, texts, *, query=False):
        np = self.np
        result = []
        with self.lock:
            for start in range(0, len(texts), MAX_BATCH):
                tokens = self.tokenizer.encode_batch(
                    [
                        (self.query_prefix if query else "") + str(text)[:3000]
                        for text in texts[start : start + MAX_BATCH]
                    ]
                )
                arrays = {
                    "input_ids": np.asarray([t.ids for t in tokens], dtype=np.int64),
                    "attention_mask": np.asarray(
                        [t.attention_mask for t in tokens], dtype=np.int64
                    ),
                    "token_type_ids": np.asarray(
                        [t.type_ids for t in tokens], dtype=np.int64
                    ),
                }
                output = self.session.run(
                    None, {k: v for k, v in arrays.items() if k in self.inputs}
                )[0]
                if output.shape != (*arrays["input_ids"].shape, DIMENSIONS):
                    raise ValueError("Invalid encoder output dimensions")
                if not np.isfinite(output).all():
                    raise ValueError("Invalid encoder output values")
                mask = arrays["attention_mask"][..., None]
                pooled = (
                    output[:, 0]
                    if self.pooling == "cls"
                    else (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
                )
                norms = np.linalg.norm(pooled, axis=1, keepdims=True)
                if (
                    not np.isfinite(pooled).all()
                    or not np.isfinite(norms).all()
                    or (norms <= 1e-12).any()
                ):
                    raise ValueError("Invalid encoder output values")
                pooled /= norms
                result.extend(pooled.astype(np.float32).tolist())
        return result


@lru_cache(maxsize=1)
def _load():
    try:
        return Encoder()
    except Exception:
        logging.getLogger(__name__).warning(
            "Semantic encoder unavailable; using text search."
        )
        return None


def encoder():
    if os.environ.get("TRACKER_SEMANTIC_SEARCH", "1") == "0":
        return None
    with LOAD_LOCK:
        model = _load()
        return model if model is not None and not model.failed else None
