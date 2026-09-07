"""Arah semantic buat post NEUTRAL (stdlib).

DUAL PROVIDER (7 Sep, hasil benchmark 150 label):
1. Cohere embed-v4.0 (kalau COHERE_API_KEY ada di env) — presisi 94%
   (thr 0.75, k=3 sepakat, LOO) vs baseline tebak-bullish 32%.
   Protokol: dokumen-vs-dokumen (SAMAN dengan benchmark; jangan pakai
   search_query utk kasus post-vs-post — skala cosine-nya beda & kalibrasi
   jadi gak berlaku).
2. MiniLM sidecar :8010 (fallback, presisi 60% di gate yang sama).

Pool = post BERLABEL jelas (bullish/bearish) dari data/sentiment/
annotation_pilot.csv — vektor Cohere di embeddings (model 'embed-v4.0',
diisi scripts/build_pool_cohere.py), vektor MiniLM di embeddings (model
paraphrase-multilingual-MiniLM-L12-v2).

Sidecar mati + key gak ada -> None (tanpa hint). Bukan pengganti label
utama: yang sudah jelas bullish/bearish tetap lexicon-v3.
"""
import hashlib
import json
import math
import os
import sqlite3
import struct
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "demo.db")
SIDECAR_URL = "http://127.0.0.1:8010"
MODEL_MINILM = "paraphrase-multilingual-MiniLM-L12-v2"
MODEL_COHERE = "embed-v4.0"

# Threshold per-provider, kalibrasi benchmark final 7 Sep (150 label,
# pass-2 reparasi gold, LOO + dedup):
#   Cohere k=3 @0.60 -> 82% (11 flip) | k=1 @0.60 -> 80% (35 flip)
#   MiniLM k=3 @0.70 -> 67% (9 flip)  | k=1 @0.70 -> 61% (23 flip)
MIN_SIM_BY_MODEL = {MODEL_COHERE: 0.60, MODEL_MINILM: 0.70}
MIN_SIM = 0.60  # default (provider utama)
TOP_K = 3
MIN_POOL = 5
_cache_pool = {}        # model -> [(postid, label, vec)]
_cache_query = {}       # sha1(text) -> vec (hindari API call ganda antar-ticker)


def _embed_minilm(text):
    req = urllib.request.Request(
        SIDECAR_URL + "/embed",
        data=json.dumps({"texts": [text[:2000]]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["vectors"][0]


def _pool(model):
    """Pool post berlabel jelas + vektornya (cache per proses).

    Sumber beda per model (PK embeddings cuma (kind, ref_id) — vektor Cohere
    GAK boleh disimpan di situ karena menimpa MiniLM):
    - Cohere  -> tabel pool_vectors (kolom label ikut disimpan)
    - MiniLM  -> embeddings (JOIN labeled)
    """
    if model not in _cache_pool:
        con = sqlite3.connect(DB_PATH, timeout=15)
        try:
            if model == MODEL_COHERE:
                rows = con.execute(
                    "SELECT ref_id, label, vector FROM pool_vectors WHERE model = ?",
                    (model,)).fetchall()
            else:
                rows = con.execute(
                    "SELECT l.postid, l.label, e.vector "
                    "FROM labeled l JOIN embeddings e ON e.kind='post' AND e.ref_id = l.postid "
                    "AND e.model = ? WHERE l.kind='post' AND l.label IN ('bullish','bearish')",
                    (model,)).fetchall()
        finally:
            con.close()
        pool = []
        for postid, label, blob in rows:
            n = len(blob) // 4
            pool.append((postid, label, struct.unpack("<" + str(n) + "f", blob)))
        _cache_pool[model] = pool
    return _cache_pool[model]


def _query_vec(text, provider):
    """Vektor query — cached antar-ticker (teks sama = vektor sama)."""
    key = hashlib.sha1((provider + "|" + text[:2000]).encode()).hexdigest()
    if key not in _cache_query:
        if provider == MODEL_COHERE:
            from ml.search import embed_cohere
            _cache_query[key] = embed_cohere.embed_texts([text], "search_document")[0]
        else:
            _cache_query[key] = _embed_minilm(text)
    return _cache_query[key]


def semantic_direction_ex(text, exclude_pid=None):
    """Hint arah dgn provider terbaik yg tersedia (Cohere -> MiniLM -> None).

    Return {direction, sim, matches:[{label,sim,postid,snippet}], provider}
    atau None kalau gak cukup bukti / semua provider mati.
    """
    providers = []
    try:
        from ml.search import embed_cohere
        if embed_cohere.available():
            providers.append(MODEL_COHERE)
    except Exception:  # noqa: BLE001
        pass
    providers.append(MODEL_MINILM)  # fallback terakhir

    last_err = None
    for model in providers:
        try:
            pool = _pool(model)
            if len(pool) < MIN_POOL:
                continue  # provider ini belum di-index -> coba fallback
            qv = _query_vec(text, model)
            qn = math.sqrt(sum(x * x for x in qv)) or 1.0
            sims = []
            for postid, label, v in pool:
                if exclude_pid is not None and postid == exclude_pid:
                    continue  # LOO: post itu sendiri gak boleh jadi bukti
                vn = math.sqrt(sum(x * x for x in v)) or 1.0
                sims.append((sum(a / qn * b / vn for a, b in zip(qv, v)), label, postid))
            sims.sort(reverse=True)
            min_sim = MIN_SIM_BY_MODEL.get(model, MIN_SIM)
            top = [s for s in sims if s[0] <= 0.99][:TOP_K]  # >0.99 = duplikat teks
            if not top or top[0][0] < min_sim or len(top) < TOP_K:
                return None
            labels = {l for _, l, _ in top}
            if len(labels) != 1:  # wajib sepakat
                return None
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                matches = []
                for c, label, postid in top:
                    row = con.execute(
                        "SELECT SUBSTR(COALESCE(text_original,''),1,120) FROM stream_posts "
                        "WHERE postid=?", (postid,)).fetchone()
                    matches.append({"label": label, "sim": round(c, 3),
                                    "postid": postid, "snippet": (row[0] if row else "")})
            finally:
                con.close()
            return {"direction": top[0][1], "sim": round(top[0][0], 3),
                    "matches": matches, "provider": model,
                    "threshold": min_sim}
        except Exception as e:  # noqa: BLE001 — provider gagal -> fallback
            last_err = e
    if last_err is not None:
        print(f"[semantic_direction] semua provider gagal: {last_err!r}", flush=True)
    return None


def semantic_direction(text):
    """Kompat lama (tanpa exclude_pid)."""
    return semantic_direction_ex(text, exclude_pid=None)


MODEL_VERSION_SEM = "semantic-knn-v1"


def resolve_direction(postid, ticker, text, per_ticker_label, per_ticker_score):
    """Resolve label per-ticker untuk ingest (dipanggil app.py).

    - label rule BUKAN neutral -> pass-through apa adanya (via "lexicon-v3").
    - label NEUTRAL + bukti semantic kuat (MIN_SIM, k=3 wajib sepakat)
      -> label jadi arah pool, skor = margin kecil searah, via MODEL_VERSION_SEM.
    - NEUTRAL tanpa bukti / semua provider mati -> tetap neutral.

    Return (label, score, model_version). Tiap resolve semantic tercatat di
    tabel semantic_direction_log (audit + bahan kalibrasi berikutnya).
    """
    if (per_ticker_label or "neutral") != "neutral":
        return per_ticker_label, per_ticker_score, "lexicon-v3"
    hint = semantic_direction_ex(text, exclude_pid=postid)
    if not hint:
        return "neutral", per_ticker_score, "lexicon-v3"
    direction = hint["direction"]
    sim = hint["sim"]
    thr = hint.get("threshold", MIN_SIM)
    margin = min(0.2, max(0.0, sim - thr))
    score = round(margin if direction == "bullish" else -margin, 3)
    for _attempt in range(4):  # audit: retry WAL-busy (collector memegang write lock)
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            con.execute("PRAGMA busy_timeout=15000")
            break
        except sqlite3.OperationalError:
            if _attempt == 3:
                print("[semantic_direction] audit skip: DB locked", flush=True)
                return direction, score, MODEL_VERSION_SEM
            import time as _t
            _t.sleep(0.4 * (_attempt + 1))
    try:
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS semantic_direction_log ("
                "postid INTEGER NOT NULL, ticker TEXT NOT NULL, direction TEXT NOT NULL,"
                " sim REAL, created_at TEXT, provider TEXT, PRIMARY KEY (postid, ticker))")
            cols = {r[1] for r in con.execute("PRAGMA table_info(semantic_direction_log)")}
            if "provider" not in cols:
                con.execute("ALTER TABLE semantic_direction_log ADD COLUMN provider TEXT")
            con.execute(
                "INSERT OR REPLACE INTO semantic_direction_log "
                "(postid, ticker, direction, sim, created_at, provider) VALUES (?,?,?,?,?,?)",
                (postid, ticker, direction, sim,
                 datetime.now(timezone.utc).isoformat(), hint.get("provider")))
            con.commit()
        finally:
            con.close()
    except Exception as _e:  # noqa: BLE001 — audit gagal gak boleh bikin ingest gagal
        print(f"[semantic_direction] audit insert gagal: {_e!r}", flush=True)
    return direction, score, MODEL_VERSION_SEM
