# Sentence encoder

Trackify uses `sentence-transformers/all-MiniLM-L6-v2`, distributed under the Apache License 2.0. The accompanying license is in `MiniLM-L6-APACHE-2.0.txt`.

Model source: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

This model was developed during Hugging Face's Community Week using pretrained MiniLM and sentence-pair contrastive training. Trackify distributes the repository's quantized ONNX export unchanged. It supplies its own bounded tokenization, mean pooling, normalization, retrieval and extractive description logic.

The exact revision and file checksums are recorded in `app/tracker/semantic_assets.json`. Trackify does not retrain these weights or send account text to Hugging Face. Downloads happen during installation/build only.
