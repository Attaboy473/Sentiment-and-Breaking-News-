"""Inference materiality IndoBERT + policy threshold (dok 9.6-9.8)."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
from ml.common import AVAILABLE, Inferencer, MODELS_DIR  # noqa: E402

MODEL_PATH = os.path.join(MODELS_DIR, "indobert-news-materiality-v1")
VERSION = "indobert-news-materiality-v1"
TRIGGER_SCORE = 0.75      # placeholder, kalibrasi nanti (dok 43)
_instance = None


def get_instance():
    global _instance
    if _instance is None and AVAILABLE and os.path.isdir(MODEL_PATH):
        _instance = Inferencer(MODEL_PATH, max_length=384)
    return _instance


def assess_materiality(headline: str, summary: str = "", tickers: list[str] | None = None) -> dict | None:
    inst = get_instance()
    if inst is None:
        return None
    inp = f"[TICKERS] {', '.join(tickers) if tickers else 'UNKNOWN'} [HEADLINE] {headline} [SUMMARY] {summary or '-'}"
    r = inst.predict([inp])[0]
    score = r["probabilities"].get("material", 0.0)
    r["materiality_score"] = score
    r["decision"] = "TRIGGER-ELIGIBLE" if (score >= TRIGGER_SCORE and tickers) else (
        "CANDIDATE" if score >= 0.50 else "IGNORE")
    r["model_version"] = VERSION
    return r
