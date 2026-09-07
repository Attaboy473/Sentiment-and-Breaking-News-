"""Index pool post berlabel (bullish/bearish) pakai Cohere embed-v4.0.

Simpan ke tabel embeddings (kind='post', model='embed-v4.0', 1536 dim).
Dipanggil manual kalau pool berlabel berubah (CSV diperbarui):
    export COHERE_API_KEY=... (env var — jangan ditulis ke file!)
    .venv-ml/Scripts/python.exe scripts/build_pool_cohere.py
"""
import csv
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

rows = list(csv.DictReader(open(os.path.join(BASE, "data", "sentiment", "annotation_pilot.csv"),
                                encoding="utf-8")))
csv_pool = [(r["postid"], r["label"]) for r in rows
            if r["label"] in ("bullish", "bearish")]

# SUMBER UTAMA: tabel labeled (label final hasil reparasi pass-2, sinkron dgn benchmark).
con = sqlite3.connect(DB)
db_pool = [(str(p), l) for p, l in con.execute(
    "SELECT postid, label FROM labeled WHERE kind='post' AND label IN ('bullish','bearish')")]
pool = db_pool if len(db_pool) >= len(csv_pool) else csv_pool
print(f"pool berlabel: {len(pool)} (dari {'DB labeled' if len(db_pool) >= len(csv_pool) else 'CSV'})")

# gabung sama teks terbaru dari DB (teks di CSV bisa terpotong)
con = sqlite3.connect(DB)
texts = []
for pid, label in pool:
    row = con.execute("SELECT COALESCE(text_original,'') FROM stream_posts WHERE postid=?",
                      (pid,)).fetchone()
    texts.append((row[0] if row else "") or "")

t0 = time.time()
vecs = embed_cohere.embed_texts(texts, "search_document")
print(f"embed Cohere: {time.time()-t0:.0f}s, dim {len(vecs[0])}")

with con:
    for (pid, label), v in zip(pool, vecs):
        con.execute(
            "INSERT OR REPLACE INTO pool_vectors (model, ref_id, label, dim, vector, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (MODEL, pid, label, len(v), struct.pack("<" + str(len(v)) + "f", *v)))
n = con.execute("SELECT COUNT(*) FROM pool_vectors WHERE model=?", (MODEL,)).fetchone()[0]
con.close()
print(f"OK: {n} vektor Cohere ter-index di pool_vectors")
