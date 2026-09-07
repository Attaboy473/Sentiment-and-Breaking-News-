"""Client Cohere embed-v4.0 (stdlib murni, token VIA ENV VAR).

Provider pilihan buat lapisan KEPUTUSAN (direction resolution) setelah
benchmark 7 Sep: presisi 94% (thr 0.75, k=3 sepakat) vs MiniLM 60%.
News margin & search box tetap MiniLM (benchmark seri/flat -> gak dipindah).

Key dibaca dari os.environ["COHERE_API_KEY"] tiap pemanggilan — JANGAN
pernah ditulis ke file/db/config. Key kosong / API down -> available()
False / exception -> pemanggil fallback ke MiniLM.
"""
import json
import os
import time
import urllib.request

API_URL = "https://api.cohere.com/v2/embed"
MODEL = "embed-v4.0"
DIM = 1536
BATCH = 96


def available():
    """True kalau key ada di env (gak ngecek network di sini — murah)."""
    return bool(os.environ.get("COHERE_API_KEY"))


def embed_texts(texts, input_type="search_document"):
    """Embed list teks -> list[list[float]]. Retry 3x dgn backoff.

    input_type: 'search_document' (index/pool) | 'search_query' (query).
    Raise Exception kalau gagal total — pemanggil yang fallback.
    """
    key = os.environ.get("COHERE_API_KEY")
    if not key:
        raise RuntimeError("COHERE_API_KEY gak ada di env")
    out = []
    for i in range(0, len(texts), BATCH):
        chunk = [str(t or "")[:2000] for t in texts[i:i + BATCH]]
        req = urllib.request.Request(
            API_URL,
            data=json.dumps({"model": MODEL, "texts": chunk,
                             "embedding_types": ["float"],
                             "input_type": input_type}).encode(),
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            method="POST")
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    out.extend(json.loads(r.read())["embeddings"]["float"])
                last = None
                break
            except Exception as e:  # noqa: BLE001 — retry dgn backoff
                last = e
                time.sleep(1.5 * (attempt + 1))
        if last is not None:
            raise last
    return out
