"""Shared ML helpers (dok IndoBERT 21-24): load-once, batch inference, fallback.

Import-protected: app demo stdlib tetep jalan walau torch/transformers gak ada.
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ML_DIR = os.path.join(BASE, "ml")
MODELS_DIR = os.path.join(BASE, "models")

AVAILABLE = False
try:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    AVAILABLE = True
except ImportError:
    torch = None


class Inferencer:
    """Satu instance per model, load sekali, batch predict. Thread-safe via lock."""

    def __init__(self, model_path: str, max_length: int = 256):
        if not AVAILABLE:
            raise RuntimeError("torch/transformers tidak terpasang (mode fallback)")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path).to(self.device)
        self.model.eval()
        self.max_length = max_length
        import threading
        self._lock = threading.Lock()

    def predict(self, texts: list[str]) -> list[dict]:
        with self._lock, torch.inference_mode():
            batch = self.tokenizer(texts, truncation=True, padding=True,
                                   max_length=self.max_length, return_tensors="pt").to(self.device)
            probs = torch.softmax(self.model(**batch).logits, dim=-1).cpu()
        id2label = self.model.config.id2label
        out = []
        for row in probs:
            i = int(row.argmax())
            out.append({
                "label": id2label[i],
                "model_score": round(float(row[i]), 4),  # dok 8.11: bukan calibrated confidence
                "probabilities": {id2label[k]: round(float(v), 4) for k, v in enumerate(row)},
            })
        return out


def status(model_path: str) -> dict:
    """Info status model buat health endpoint."""
    if not AVAILABLE:
        return {"status": "fallback", "model_version": None, "device": None,
                "reason": "torch/transformers tidak terpasang"}
    if not os.path.isdir(model_path):
        return {"status": "not_trained", "model_version": None, "device": None,
                "reason": "checkpoint belum ada"}
    try:
        meta = json.load(open(os.path.join(model_path, "meta.json"), encoding="utf-8"))
        return {"status": "ok", "model_version": meta.get("model_version"), "device": "cuda" if torch.cuda.is_available() else "cpu"}
    except Exception as e:
        return {"status": "error", "model_version": None, "device": None, "reason": str(e)}
