"""Embedding sidecar — HTTP kecil yang nge-embed teks pakai MiniLM lokal.

Jalankan dari venv ML:
  .venv-ml/Scripts/python.exe ml/search/server.py   (default port 8010)

App demo (stdlib) manggil ini via POST /embed buat /api/search.
Kalau sidecar mati -> app otomatis fallback ke keyword search,
demo tetep jalan 1 perintah tanpa ML (janji stdlib tetap aman).
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from transformers import AutoModel, AutoTokenizer

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_DIR = os.path.join(BASE, "models", "_hf_base", "paraphrase-multilingual-MiniLM-L12-v2")
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
MAX_LEN = 128  # sama dengan indexer (scripts/build_embeddings.py)

tok = None
model = None


def load():
    global tok, model
    if model is None:
        tok = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        model = AutoModel.from_pretrained(MODEL_DIR, local_files_only=True)
        model.eval()


@torch.inference_mode()
def embed(texts):
    b = tok([str(t)[:2000] for t in texts], truncation=True, max_length=MAX_LEN,
            padding=True, return_tensors="pt")
    hidden = model(**b).last_hidden_state
    mask = b["attention_mask"].unsqueeze(-1).float()
    emb = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)  # mean pooling
    emb = torch.nn.functional.normalize(emb, p=2, dim=1)
    return emb.tolist()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "model": MODEL_NAME})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/embed":
            self._json({"error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            texts = data.get("texts", [])
            if not isinstance(texts, list) or not texts or len(texts) > 32:
                self._json({"error": "texts (list 1..32) wajib"}, 400)
                return
            self._json({"model": MODEL_NAME, "vectors": embed(texts)})
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 500)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    args = ap.parse_args()
    load()
    print(f"embedding sidecar ready di http://127.0.0.1:{args.port} ({MODEL_NAME})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
