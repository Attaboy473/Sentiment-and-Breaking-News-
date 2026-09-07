"""Semantic search engine — Cohere embed-v4.0 penuh (7 Sep).

Mode:
  1. "semantic" : COHERE_API_KEY ada di env + index terisi
                  -> query di-embed (input_type=search_query), cosine vs
                     tabel `embeddings` (model 'embed-v4.0', 1536 dim)
  2. "keyword"  : key gak ada / API gagal -> FTS sederhana LIKE + skoring
                  (demo tetap jalan tanpa internet)

Index dibangun scripts/build_embeddings_cohere.py (embed-v4.0), simpan di
demo.db tabel `embeddings(kind, ref_id, model, dim, vector)`. Vektor MiniLM
lama sudah dipensiunkan (backup: demo_backup_minilm.db).
"""
import json
import math
import os
import re
import struct
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "demo.db")
EMBED_MODEL = "embed-v4.0"
TOP_K_DEFAULT = 20


def sidecar_available() -> bool:
    """Nama lama dipertahankan buat app.py — sekarang berarti 'Cohere siap?'."""
    try:
        from ml.search import embed_cohere
        return embed_cohere.available()
    except Exception:  # noqa: BLE001
        return False


def _embed_query(text):
    """Vektor query via API Cohere (input_type=search_query). None kalau gagal."""
    try:
        from ml.search import embed_cohere
        return embed_cohere.embed_texts([text[:2000]], "search_query")[0]
    except Exception:  # noqa: BLE001
        return None


def _blob_to_vec(blob):
    n = len(blob) // 4
    return list(struct.unpack("<" + str(n) + "f", blob))


# ---------------------------------------------------------------------------
# sumber konten (dibaca langsung dari demo.db)
# ---------------------------------------------------------------------------
def _load_docs(con):
    """ref_id -> {kind, text, meta} untuk semua kind yang di-index."""
    docs = {}
    for postid, text, created, likes in con.execute(
            "SELECT postid, text_original, COALESCE(created_at_utc,''), COALESCE(likes,0) "
            "FROM stream_posts"):
        docs["post:" + str(postid)] = {
            "kind": "post", "ref_id": str(postid), "text": text or "",
            "created_at": created, "likes": likes}
    for url_hash, title, summary, src, pub in con.execute(
            "SELECT url_hash, COALESCE(title,''), COALESCE(summary,''), "
            "COALESCE(source_name,''), COALESCE(published_at,'') FROM news_articles"):
        docs["news:" + url_hash] = {
            "kind": "news", "ref_id": url_hash, "text": (title + " " + summary).strip(),
            "title": title, "source": src, "published_at": pub}
    return docs


# ---------------------------------------------------------------------------
# mode literal (query ticker: "bbca", "$BBCA", "BBCA BBRI")
# ---------------------------------------------------------------------------
_TICKER = re.compile(r"^[A-Z]{4}$")


def _parse_query(query):
    """Return (mode, tokens_upper): 'literal' kalo SEMUA token = 4 huruf alpha."""
    toks = [t for t in re.split(r"[^0-9A-Za-z]+", (query or "").strip()) if t]
    up = [t.upper() for t in toks]
    if up and all(_TICKER.match(t) for t in up):
        return "literal", up
    return "hybrid", up


def _search_literal(con, tickers, top_k):
    """Cari dokumen yang menyebut ticker secara literal, urut
    jumlah kecocokan -> likes -> waktu terbaru."""
    scored = []
    for d in _load_docs(con).values():
        if not d["text"].strip():
            continue
        up = d["text"].upper()
        hits = [t for t in tickers if t in up]
        if not hits:
            continue
        scored.append(dict(d, score=1.0, _hits=len(hits),
                           _likes=d.get("likes") or 0,
                           _when=d.get("created_at") or d.get("published_at") or ""))
    scored.sort(key=lambda d: (d["_hits"], d["_likes"], d["_when"]), reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# mode semantic (Cohere)
# ---------------------------------------------------------------------------
def _search_semantic(con, query, top_k, boost_tokens=None):
    qv = _embed_query(query)
    if qv is None:
        raise RuntimeError("embed Cohere gagal (key/API)")
    qn = math.sqrt(sum(x * x for x in qv)) or 1.0
    docs = _load_docs(con)
    scored = []
    for kind, ref_id, model, _dim, blob in con.execute(
            "SELECT kind, ref_id, model, dim, vector FROM embeddings"):
        if model != EMBED_MODEL:
            continue
        doc = docs.get(kind + ":" + ref_id)
        if not doc or not doc["text"].strip():
            continue
        v = _blob_to_vec(blob)
        if len(v) != len(qv):
            continue
        vn = math.sqrt(sum(x * x for x in v)) or 1.0
        cos = sum(a / qn * b / vn for a, b in zip(qv, v))
        if boost_tokens:  # hybrid: dokumen yang nyebut token query dapet boost
            up = doc["text"].upper()
            hits = sum(1 for t in boost_tokens if t in up)
            if hits:
                cos += 0.15 * min(hits, 3)
        scored.append(dict(doc, score=round(cos, 4)))
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# mode keyword (fallback stdlib murni, tanpa internet)
# ---------------------------------------------------------------------------
def _tokens(text):
    t = (text or "").lower()
    clean = "".join(c if c.isalnum() or c.isspace() else " " for c in t)
    return [w for w in clean.split() if w]


def _search_keyword(con, query, top_k):
    qtok = _tokens(query)
    if not qtok:
        return []
    qset = set(qtok)
    docs = [d for d in _load_docs(con).values() if d["text"].strip()]
    scored = []
    for d in docs:
        dtok = _tokens(d["text"])
        if not dtok:
            continue
        hit = sum(1 for q in qset if q in dtok)          # kata query yang ketemu
        if hit == 0:
            continue
        cov = hit / len(qset)                            # coverage query
        freq = sum(dtok.count(q) for q in qset)          # frekuensi mentah
        scored.append(dict(d, score=round(cov + 0.05 * min(freq, 10) / 10, 4)))
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# API utama
# ---------------------------------------------------------------------------
def search(query, top_k=TOP_K_DEFAULT, kinds=None):
    """Cari post + berita. Return {mode, query, indexed, results:[...]}."""
    query = (query or "").strip()
    if not query:
        return {"mode": "empty", "query": query, "indexed": 0, "results": []}
    con = sqlite3.connect(DB_PATH)
    try:
        n_indexed = con.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model=?", (EMBED_MODEL,)).fetchone()[0]
        qmode, toks = _parse_query(query)
        use_semantic = sidecar_available() and n_indexed > 0
        try:
            if qmode == "literal":
                # query ticker ("bbca", "$BBCA BBCA") -> cari literal, bukan makna
                results = _search_literal(con, toks, top_k)
                mode = "literal"
            elif use_semantic:
                results = _search_semantic(con, query, top_k, boost_tokens=toks)
                mode = "semantic"
            else:
                results = _search_keyword(con, query, top_k)
                mode = "keyword"
        except Exception:  # noqa: BLE001 — semantic gagal mid-request -> keyword
            results = _search_keyword(con, query, top_k)
            mode = "keyword"
        if kinds:
            results = [r for r in results if r.get("kind") in kinds]
        out = []
        for r in results:
            text = r.get("text", "")
            item = {k: v for k, v in r.items() if k != "text"}
            item["snippet"] = text[:280] + ("…" if len(text) > 280 else "")
            out.append(item)
        return {"mode": mode, "query": query, "indexed": n_indexed, "results": out}
    finally:
        con.close()
