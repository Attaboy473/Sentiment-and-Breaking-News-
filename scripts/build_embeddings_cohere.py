"""Index SEMUA dokumen (post + news) pakai Cohere embed-v4.0.

Ganti dari scripts/build_embeddings.py (MiniLM, dipensiunkan 7 Sep).
Simpan ke demo.db tabel embeddings (model='embed-v4.0', 1536 dim).

Pakai:
    export COHERE_API_KEY=...   (env var — JANGAN ditulis ke file!)
    .venv-ml/Scripts/python.exe scripts/build_embeddings_cohere.py
"""
import json
import os
import sqlite3
import struct
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from ml.search import embed_cohere  # noqa: E402

DB = os.path.join(BASE, "demo.db")
MODEL = "embed-v4.0"
BATCH = 48  # aman utk teks panjang (limit token per request)


def chunked(texts):
    for i in range(0, len(texts), BATCH):
        yield texts[i:i + BATCH]


con = sqlite3.connect(DB)
docs = []
for pid, text in con.execute(
        "SELECT postid, COALESCE(text_original,'') FROM stream_posts"):
    if text.strip():
        docs.append(("post", str(pid), text[:2000]))
for uh, title, summary in con.execute(
        "SELECT url_hash, COALESCE(title,''), COALESCE(summary,'') FROM news_articles"):
    t = (title + " " + summary).strip()
    if t:
        docs.append(("news", uh, t[:2000]))
print(f"dokumen: {len(docs)} (post {sum(1 for d in docs if d[0]=='post')}, "
      f"news {sum(1 for d in docs if d[0]=='news')})")

t0 = time.time()
done = 0
with con:
    for chunk in chunked([d[2] for d in docs]):
        vecs = embed_cohere.embed_texts(chunk, "search_document")
        for (kind, ref_id, _), v in zip(docs[done:done + len(chunk)], vecs):
            con.execute(
                "INSERT OR REPLACE INTO embeddings (kind, ref_id, model, dim, vector, created_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (kind, ref_id, MODEL, len(v), struct.pack("<" + str(len(v)) + "f", *v)))
        done += len(chunk)
        print(f"  {done}/{len(docs)}")
n = con.execute("SELECT COUNT(*) FROM embeddings WHERE model=?", (MODEL,)).fetchone()[0]
con.close()
print(f"OK: {n} vektor embed-v4.0 ter-index ({time.time()-t0:.0f}s)")
