"""Inference sentiment IndoBERT + fallback lexicon-v3 (dok 8.14, 24).

Model-first kalau checkpoint ada; kalau gak, lexicon-v3 yang sekarang jalan.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
from ml.common import AVAILABLE, Inferencer, MODELS_DIR  # noqa: E402

MODEL_PATH = os.path.join(MODELS_DIR, "indobert-stockbit-sentiment-v1")
VERSION = "indobert-stockbit-sentiment-v1"
_instance = None


def get_instance():
    global _instance
    if _instance is None and AVAILABLE and os.path.isdir(MODEL_PATH):
        _instance = Inferencer(MODEL_PATH, max_length=256)
    return _instance


def predict_sentiment(ticker: str, target_context: str, full_text: str = None) -> dict | None:
    """Return dict (label, model_score, probabilities, model_version) atau None = fallback.

    Input = teks polos, SAMA dengan format training (ticker sudah ada di teks
    sebagai cashtag; gak ada prefix/pasangan [SEP] tambahan)."""
    inst = get_instance()
    if inst is None:
        return None
    text = full_text or target_context
    r = inst.predict([text])[0]
    r["model_version"] = VERSION
    return r
