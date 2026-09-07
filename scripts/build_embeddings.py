"""Bangun index embedding semantic search (posts + news) ke SQLite.

Jalankan dari venv ML:
  .venv-ml/Scripts/python.exe scripts/build_embeddings.py

Model: paraphrase-multilingual-MiniLM-L12-v2 (lokal, gratis).
- Incremental: post/berita yang udah ada vektornya di-skip.
- Re-run aman: INSERT OR REPLACE per (kind, ref_id).
- Vektor = float32 ternormalisasi L2 -> cosine = dot product.
"""
import argparse
import datetime
import os
import sqlite3
import struct
import sys

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE, "models", "_hf_base", "paraphrase-multilingual-MiniLM-L12-v2")
DEFAULT_DB = os.path.join(BASE, "demo.db")

EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384
MAX_LEN = 128  # post pendek + lead berita; hemat CPU


def load_encoder():
    tok = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
    model = AutoModel.from_pretrained(MODEL_DIR, local_files_only=True)
    model.eval()
    return tok, model


@torch.inference_mode()
def encode(tok, model, texts, batch_size=32):
    out = []
    for i in range(0, len(texts), batch_size):
        chunk = [t[:2000] for t in texts[i:i + batch_size]]
        b = tok(chunk, truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt")
        hidden = model(**b).last_hidden_state          # (B, L, H)
        mask = b["attention_mask"].unsqueeze(-1).float()
        emb = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)  # mean pooling
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        out.append(emb.cpu().numpy().astype(np.float32))
        done = min(i + batch_size, len(texts))
        print(f"  encoded {done}/{len(texts)}", flush=True)
    return np.vstack(out)


def ensure_table(db):
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            kind TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL,
            created_at TEXT,
            PRIMARY KEY (kind, ref_id)
        )
    """)
    con.commit()
    return con


def fetch_sources(con):
    posts = con.execute(
        "SELECT postid, text_original FROM stream_posts ORDER BY postid").fetchall()
    news = con.execute(
        "SELECT url_hash, COALESCE(title,'') || ' ' || COALESCE(summary,'') "
        "FROM news_articles ORDER BY url_hash").fetchall()
    return posts, news


def existing(con, kind):
    return {r[0] for r in con.execute(
        "SELECT ref_id FROM embeddings WHERE kind=? AND model=?", (kind, EMBED_MODEL_NAME))}


def store(con, kind, rows, vecs):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    payload = [(kind, str(rid), EMBED_MODEL_NAME, DIM, v.astype(np.float32).tobytes(), now)
               for (rid, _), v in zip(rows, vecs)]
    con.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?,?)", payload)
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0, help="batasi jumlah (debug)")
    ap.add_argument("--force", action="store_true", help="re-encode semua, timpa yang lama")
    args = ap.parse_args()

    con = ensure_table(args.db)
    posts, news = fetch_sources(con)
    print(f"sumber: {len(posts)} post, {len(news)} berita")

    tok, model = load_encoder()
    print(f"model siap: {EMBED_MODEL_NAME} (dim={DIM})")

    done_posts = set() if args.force else existing(con, "post")
    done_news = set() if args.force else existing(con, "news")

    todo_posts = [(pid, t) for pid, t in posts if str(pid) not in done_posts]
    todo_news = [(h, t) for h, t in news if str(h) not in done_news]
    print(f"perlu encode: {len(todo_posts)} post, {len(todo_news)} berita "
          f"(sudah ada: {len(done_posts)} post, {len(done_news)} berita)")

    if args.limit:
        todo_posts = todo_posts[:args.limit]
        todo_news = todo_news[:args.limit]

    if todo_posts:
        print("encode posts...")
        store(con, "post", todo_posts, encode(tok, model, [t for _, t in todo_posts], args.batch_size))
    if todo_news:
        print("encode news...")
        store(con, "news", todo_news, encode(tok, model, [t for _, t in todo_news], args.batch_size))

    n = con.execute("SELECT kind, COUNT(*) FROM embeddings GROUP BY kind").fetchall()
    print("SELESAI. Isi tabel embeddings:", dict(n))


if __name__ == "__main__":
    main()
