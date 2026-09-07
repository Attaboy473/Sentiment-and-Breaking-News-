#!/usr/bin/env python3
"""
MarketForge Demo — standalone single-file app.

Demonstrates the coding/concepts from MarketForge_Complete_Project_Bundle:
  1. Stockbit Stream sentiment pipeline   (bundle folder 02)
     - collector -> rolling SQLite store -> lexicon sentiment -> aggregates
  2. Breaking news rule engine            (bundle folders 03 + 04)
     - RSS poll -> rule score 0-100 -> explainable reasons
  3. Material event -> stale recommendation (plan section 8)
     - triggered event flips recommendation board to STALE, simulated
       targeted reassessment afterwards
  4. Audit trail                          (Ranah blueprint principle)
     - every event keeps its full reasoning JSON, inspectable in UI

STANDALONE: Python 3.10+ stdlib only. No Docker, no Postgres, no Redis.
Run:  python app.py            -> open printed URL (default try :8004)
      python app.py --selftest -> run quick unit checks then exit

Honesty labels (per Implementation Review 2026): sentiment here is a
DEMO-GRADE lexicon heuristic, recommendations are DUMMY baseline data,
and the LLM validator is replaced by a transparent rule-only proxy.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "demo.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.getenv("DEMO_PORT", "8004"))
COLLECT_INTERVAL = int(os.getenv("DEMO_COLLECT_INTERVAL", "300"))   # >= 300 (CDN cache 300s)
BREAKING_INTERVAL = int(os.getenv("DEMO_BREAKING_INTERVAL", "60"))
REASSESS_DELAY = int(os.getenv("DEMO_REASSESS_DELAY", "20"))        # simulated reassessment
NEWS_RETENTION_H = int(os.getenv("DEMO_NEWS_RETENTION_H", "48"))    # retensi artikel news (Phase 1)

WIB = timezone(timedelta(hours=7))
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

TICKERS = ["BBCA", "BBRI", "BMRI", "TLKM", "ANTM", "INCO", "ADRO", "BUMI", "GOTO", "UNTR"]

STOCKBIT_URL = "https://exodus.stockbit.com/stream/non-login/symbol/{sym}"
STOCKBIT_HEADERS = {
    "accept": "application/json",
    "origin": "https://stockbit.com",
    "referer": "https://stockbit.com/",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
}

RSS_SOURCES = [
    {"name": "ANTARA Bursa", "url": "https://www.antaranews.com/rss/ekonomi-bursa.xml", "priority": 1.0},
    {"name": "ANTARA Finansial", "url": "https://www.antaranews.com/rss/ekonomi-finansial.xml", "priority": 0.9},
]

# rule-score constants ported from bundle scripts/breaking_event_pipeline.py
IMPACT_KEYWORDS = {
    "suspensi": 28, "pailit": 30, "gagal bayar": 30, "fraud": 28, "korupsi": 24,
    "merger": 24, "akuisisi": 22, "rights issue": 20, "private placement": 18,
    "buyback": 18, "delisting": 26, "msci": 18, "ftse": 18, "laba melonjak": 18,
    "laba anjlok": 18, "rugi bersih": 18, "kontrak jumbo": 20, "izin dicabut": 25,
    "produksi dihentikan": 24, "kebakaran": 22, "ledakan": 25, "dividen": 14,
    "ipo": 15, "stock split": 16, "kontrak baru": 16,
}
HARD_EVENTS = {"suspensi", "pailit", "gagal bayar", "fraud", "delisting",
               "izin dicabut", "produksi dihentikan", "ledakan"}
# P3.5: denial guard - judul berisi frasa ini -> keyword material disuppress
# (dok V2 13.2: "membantah isu pailit" bukan event pailit)
DENIAL_PHRASES = [
    "membantah", "bantah", "bantahan", "sanggah", "menyanggah", "klarifikasi",
    "tidak berdampak", "tidak terdampak", "kembali normal", "belum dikonfirmasi",
    "rumor", "isu",
]
# P3: entity resolver - alias nama emiten -> ticker, dgn confidence per alias
# (urutan bebas; yang dipakai = confidence TERTINGGI yang match)
TICKER_ALIASES = {
    "BBCA": {"bank central asia": 0.95, "bank bca": 0.95, "bca": 0.85},
    "BBRI": {"bank rakyat indonesia": 0.95, "bank bri": 0.95, "bri": 0.85},
    "BMRI": {"bank mandiri": 0.95, "mandiri": 0.7},
    "TLKM": {"telkom indonesia": 0.95, "telkom": 0.9, "telkomsel": 0.8, "indihome": 0.7},
    "ANTM": {"aneka tambang": 0.95, "antam": 0.9},
    "INCO": {"vale indonesia": 0.95, "pt vale": 0.9, "vale": 0.75},
    "ADRO": {"adaro energy": 0.95, "adaro": 0.9, "alamtri": 0.8},
    "BUMI": {"bumi resources": 0.95},
    "GOTO": {"goto gojek tokopedia": 0.95, "gojek": 0.85, "tokopedia": 0.85, "goto": 0.7},
    "UNTR": {"united tractors": 0.95, "untr": 0.7},
}
ENTITY_CONF_HIGH = 0.8      # minimal utk bonus skor breaking (+15/+12)
ENTITY_CONF_RELATION = 0.7  # minimal utk bikin relasi post<->ticker
CANDIDATE_THRESHOLD = 45.0
# Eskalasi by-context berita: rule "hampir" + semantic yakin material ->
# dinaikin tipis ke atas threshold (kalibrasi margin dari 40 pilot; floor
# sengaja deket threshold biar eskalasi jarang & terkontrol).
ESCALATE_FLOOR = 40.0
ESCALATE_MARGIN = 0.20  # skala Cohere (kalibrasi 40 pilot, flat 0.20-0.50)
MAX_ITEM_AGE_MIN = 180

SECTOR_ALIASES = {
    "Financials": ["bank", "perbankan", "kredit", "bunga", "ojk"],
    "Energy": ["batubara", "batu bara", "minyak", "gas", "energi"],
    "Basic Materials": ["nikel", "emas", "tembaga", "tambang", "smelter"],
    "Technology": ["teknologi", "digital", "data center", "e-commerce", "fintech"],
    "Infrastructure": ["infrastruktur", "telko", "menara", "jalan tol"],
}

# Demo-grade sentiment lexicon (NOT calibrated - see README)
LEX_BULL = {
    "bull", "bullish", "naik", "melejit", "melonjak", "cuan", "profit", "breakout",
    "akumulasi", "suntik", "gap up", "lampu ijo", "ijo", "hijau", "gacor", "ara",
    "buy", "sokong", "gemoy", "mantap", "yakin", "kencang", "sehat", "back to mahkota",
}
LEX_BEAR = {
    "bear", "bearish", "turun", "anjlok", "jeblok", "rugi", "cutloss", "potong bongkar",
    "panic", "panik", "dump", "macet", "busuk", "bocor", "lampu merah", "merah",
    "sell", "bongkar", "distribusi", "arb", "susah gerak", "sempit", "bocah cc", "gap down",
}
NEGATORS = {"gak", "ga", "nggak", "ngga", "tidak", "gk", "tdk", "jangan", "bukan"}
# v2: normalisasi slang & intensifier (temuan riset: bahasa stream sangat informal)
SLANG_NORM = {
    "naaik": "naik", "naikk": "naik", "naikkk": "naik", "naiik": "naik",
    "turunn": "turun", "turuuun": "turun", "anjlokk": "anjlok", "anjloooq": "anjlok",
    "jeblokk": "jeblok", "cuann": "cuan", "cuannn": "cuan", "gacorr": "gacor",
    "gacorrr": "gacor", "kencengg": "kencang", "sempitt": "sempit", "sokongg": "sokong",
    "gemoyy": "gemoy", "banyakk": "banyak", "mantapp": "mantap", "gasss": "gas",
}
INTENSIFIERS = {"banget": 1.5, "bgt": 1.5, "gila": 1.4, "gilak": 1.4, "parah": 1.3,
                "sangat": 1.4, "sungguh": 1.3, "bener": 1.2, "dahsyat": 1.4}
# P3.5: intent-flip frasa - negator+kata harga; nilai dict = arah final
INTENT_FLIP = {
    ("gak", "rugi"): 1, ("ga", "rugi"): 1, ("nggak", "rugi"): 1, ("ngga", "rugi"): 1,
    ("tidak", "rugi"): 1, ("gk", "rugi"): 1, ("tdk", "rugi"): 1, ("bukan", "rugi"): 1,
    ("gak", "turun"): 1, ("tidak", "turun"): 1, ("gak", "anjlok"): 1, ("gak", "jeblok"): 1,
    ("gak", "bocor"): 1, ("gak", "panik"): 1, ("gak", "panic"): 1, ("gak", "macet"): 1,
    ("gak", "naik"): -1, ("ga", "naik"): -1, ("gak", "gacor"): -1, ("gak", "cuan"): -1,
    ("belum", "naik"): -1,
}

HEALTH = {
    "stockbit": {"status": "init", "last_ok": None, "last_error": None, "posts": 0, "new": 0, "overlap": None},
    "rss": {"status": "init", "last_ok": None, "last_error": None, "sources_ok": 0, "candidates": 0},
    "started_at": None,
    "lock": threading.Lock(),
}

# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")  # P0: FK aktif, baris join orphan ditolak
    return c


def init_db() -> None:
    with conn() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS stream_posts (
            postid INTEGER PRIMARY KEY,
            text_original TEXT NOT NULL,
            created_at_src TEXT,
            created_at_utc TEXT,
            likes INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0,
            dislikes INTEGER DEFAULT 0,
            author_hash TEXT,
            flags TEXT,
            raw_payload TEXT,
            collected_at TEXT
        );
        CREATE TABLE IF NOT EXISTS stream_post_tickers (
            postid INTEGER NOT NULL REFERENCES stream_posts(postid) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            relation_source TEXT,
            confidence REAL,
            PRIMARY KEY (postid, ticker)
        );
        CREATE TABLE IF NOT EXISTS sentiment_predictions (
            postid INTEGER NOT NULL,
            model_version TEXT NOT NULL,
            sentiment TEXT, intent TEXT, score REAL, confidence REAL,
            processed_at TEXT,
            PRIMARY KEY (postid, model_version)
        );
        CREATE TABLE IF NOT EXISTS sentiment_per_ticker (
            postid INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            model_version TEXT NOT NULL,
            sentiment TEXT, score REAL, processed_at TEXT,
            PRIMARY KEY (postid, ticker, model_version)
        );
        CREATE TABLE IF NOT EXISTS breaking_events (
            event_hash TEXT PRIMARY KEY,
            source_name TEXT, source_url TEXT, headline TEXT,
            published_at TEXT, detected_at TEXT,
            rule_score REAL, reasons TEXT, matched_keywords TEXT,
            tickers TEXT, sectors TEXT,
            confidence REAL, severity TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS news_articles (
            url_hash TEXT PRIMARY KEY,
            source_name TEXT, source_url TEXT, title TEXT, summary TEXT,
            published_at TEXT, fetched_at TEXT,
            rule_score REAL, tickers TEXT, sectors TEXT, keywords TEXT,
            category TEXT
        );
        CREATE TABLE IF NOT EXISTS recommendations (
            ticker TEXT PRIMARY KEY,
            score INTEGER, grade TEXT, label TEXT,
            updated_at TEXT,
            stale INTEGER DEFAULT 0,
            stale_reason TEXT, stale_event_hash TEXT, stale_at TEXT,
            reassessed_at TEXT, reassessed_note TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        """)
        # P0: bersihkan orphan join dari data lama (jalan sekali tiap start)
        c.execute("DELETE FROM stream_post_tickers WHERE postid NOT IN (SELECT postid FROM stream_posts)")
        # semantic materiality: kolom opsional (idempotent — aman kalau udah ada)
        for _stmt in ("ALTER TABLE news_articles ADD COLUMN sem_margin REAL",
                      "ALTER TABLE news_articles ADD COLUMN sem_label TEXT",
                      "CREATE TABLE IF NOT EXISTS semantic_direction_log ("
                      "postid INTEGER NOT NULL, ticker TEXT NOT NULL, direction TEXT NOT NULL,"
                      " sim REAL, created_at TEXT, PRIMARY KEY (postid, ticker))"):
            try:
                c.execute(_stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        c.execute("DELETE FROM sentiment_predictions WHERE postid NOT IN (SELECT postid FROM stream_posts)")
        c.execute("DELETE FROM sentiment_per_ticker WHERE postid NOT IN (SELECT postid FROM stream_posts)")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def wib_str(dt: datetime) -> str:
    return dt.astimezone(WIB).strftime("%d %b %H:%M")


def author_hash(username: str) -> str:
    return "sb:" + hashlib.sha1(f"mf-demo:{username}".encode()).hexdigest()[:10]


# --------------------------------------------------------------------------
# Phase 2: security & data hygiene (external content = untrusted)
# --------------------------------------------------------------------------
PII_FIELDS = {
    "username", "user", "userid", "user_id", "author", "author_id",
    "full_name", "name", "bio", "email", "avatar", "avatar_url", "profile_url",
}
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def sanitize_post(p: dict) -> dict:
    """Raw payload SEBELUM persist: field identitas/PII dibuang (username dsb
    gak ikut tersimpan — yang ke-save cuma author_hash). Nested dict ikut dibersihin."""
    out = {}
    for k, v in p.items():
        if str(k).lower() in PII_FIELDS:
            continue
        if isinstance(v, dict):
            v = sanitize_post(v)
        out[k] = v
    return out


def clean_text(s: str | None, limit: int = 8000) -> str:
    """Teks RSS: strip tag HTML + entity + normalisasi whitespace + batasi panjang."""
    if not s:
        return ""
    s = html.unescape(TAG_RE.sub(" ", s))
    return WS_RE.sub(" ", s).strip()[:limit]


# --------------------------------------------------------------------------
# Sentiment (demo-grade lexicon heuristic)
# --------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^a-z0-9$\s]", " ", t)
    return [w for w in SPACE_RE.sub(" ", t).split() if w]


def sentiment_score(text: str) -> tuple[str, float, list[str]]:
    """Returns (label, score -1..1, matched_hits).
    P3.5: bigram pass lebih dulu - frasa multi-kata dari kamus ("gap down") +
    intent-flip frasa ("gak rugi" = bullish). Kata kedua bigram di-skip di
    unigram pass biar gak dihitung dobel."""
    toks = [_norm_token(w) for w in tokenize(text)]
    hits: list[str] = []
    bull = bear = 0.0
    skip: set[int] = set()
    for i in range(len(toks) - 1):  # P3.5: context pass (bigram)
        a, b = toks[i], toks[i + 1]
        pair, big = (a, b), f"{a} {b}"
        if pair in INTENT_FLIP:
            skip.add(i + 1)
            hits.append("!" + big)
            if INTENT_FLIP[pair] > 0:
                bull += 1
            else:
                bear += 1
        elif big in LEX_BULL:
            skip.add(i + 1)
            hits.append(big)
            bull += 1
        elif big in LEX_BEAR:
            skip.add(i + 1)
            hits.append(big)
            bear += 1
        elif a in NEGATORS and (b in LEX_BULL or b in LEX_BEAR):
            skip.add(i + 1)
            hits.append("!" + big)
            if b in LEX_BULL:
                bull += 1
            else:
                bear += 1
    for i, w in enumerate(toks):
        if i in skip or w in NEGATORS:
            continue
        val = 1 if w in LEX_BULL else (-1 if w in LEX_BEAR else 0)
        if val == 0:
            continue
        negated = any(toks[j] in NEGATORS for j in range(max(0, i - 2), i))
        if negated:
            val = -val
        hits.append(("!" if negated else "") + w)
        if val > 0:
            bull += 1
        else:
            bear += 1
    total = bull + bear
    if total == 0:
        return "neutral", 0.0, hits
    score = round((bull - bear) / total, 2)
    label = "bullish" if score >= 0.34 else ("bearish" if score <= -0.34 else "neutral")
    return label, score, hits


def _norm_token(w: str) -> str:
    w = SLANG_NORM.get(w, w)
    if len(w) > 4 and w not in LEX_BULL and w not in LEX_BEAR:
        w2 = re.sub(r"(.)\1{2,}", r"\1", w)  # "naaaiiik" -> "naik"
        if w2.endswith("q"):
            w2 = w2[:-1] + "k"  # leetspeak: "anjloq" -> "anjlok"
        if w2 in LEX_BULL or w2 in LEX_BEAR:
            w = w2
    return w


def sentiment_for_ticker(text: str, ticker: str) -> tuple[str, float] | None:
    """Sentiment v2: atribusi per-cashtag. Teks dipecah per $TICKER: kata SETELAH
    $TICKER jadi miliknya sampai cashtag berikutnya; teks sebelum cashtag pertama
    ikut cashtag pertama. Post multi-ticker gak lagi "sama rata" antar ticker.
    Return None kalau $TICKER gak muncul di teks."""
    t = text.lower()
    t = re.sub(r"https?://\S+", " ", t)
    toks = [_norm_token(w) for w in re.findall(r"[a-z0-9]+|\$[a-z]{4}", t)]
    sym = "$" + ticker.lower()
    boundaries = [i for i, w in enumerate(toks) if w.startswith("$")]
    if not any(toks[i] == sym for i in boundaries):
        return None
    bull = bear = 0.0
    for idx, p in enumerate(boundaries):
        if toks[p] != sym:
            continue
        if idx == 0:
            seg = toks[:p] + toks[p + 1:boundaries[1] if len(boundaries) > 1 else len(toks)]
        else:
            nxt = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(toks)
            seg = toks[p + 1:nxt]
        skip: set[int] = set()  # P3.5: context pass per segmen
        for i in range(len(seg) - 1):
            a, b = seg[i], seg[i + 1]
            pair, big = (a, b), f"{a} {b}"
            if pair in INTENT_FLIP:
                skip.add(i + 1)
                if INTENT_FLIP[pair] > 0:
                    bull += 1.0
                else:
                    bear += 1.0
            elif big in LEX_BULL:
                skip.add(i + 1)
                bull += 1.0
            elif big in LEX_BEAR:
                skip.add(i + 1)
                bear += 1.0
            elif a in NEGATORS and (b in LEX_BULL or b in LEX_BEAR):
                skip.add(i + 1)
                if b in LEX_BULL:
                    bull += 1.0
                else:
                    bear += 1.0
        for i, w in enumerate(seg):
            if i in skip or w in NEGATORS:
                continue
            val = 1 if w in LEX_BULL else (-1 if w in LEX_BEAR else 0)
            if val == 0:
                continue
            if any(seg[j] in NEGATORS for j in range(max(0, i - 2), i)):
                val = -val
            weight = 1.0
            for j in range(max(0, i - 2), i):
                if seg[j] in INTENSIFIERS:
                    weight = INTENSIFIERS[seg[j]]
            if val > 0:
                bull += weight
            else:
                bear += weight
    total = bull + bear
    if total == 0:
        return ("neutral", 0.0)
    score = round((bull - bear) / total, 2)
    label = "bullish" if score >= 0.34 else ("bearish" if score <= -0.34 else "neutral")
    return (label, score)


# --------------------------------------------------------------------------
# Stockbit collector (bundle 02 pipeline)
# --------------------------------------------------------------------------
def fetch_stockbit(sym: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(STOCKBIT_URL.format(sym=sym), headers=STOCKBIT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_src_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB)
    except Exception:
        return None


def collect_once() -> dict:
    """Poll every watchlist ticker once (staggered). Insert new posts."""
    stats = {"fetched": 0, "new": 0, "overlap": None, "errors": 0}
    prev_ids: set[int] = set()
    with conn() as c:
        row = c.execute(
            "SELECT postid FROM stream_posts ORDER BY collected_at DESC LIMIT 60"
        ).fetchall()
        prev_ids = {r["postid"] for r in row}

    seen_this_round: list[int] = []
    for i, sym in enumerate(TICKERS):
        try:
            data = fetch_stockbit(sym)
            posts = data.get("data") or []
        except Exception as exc:  # noqa: BLE001
            # fallback dari riset: halaman SSR /symbol/{SYM} = 30 post yang sama
            try:
                posts = fetch_stockbit_fallback(sym)
                HEALTH["stockbit"]["last_error"] = f"{sym}: API gagal ({exc}) -> fallback SSR OK"
            except Exception as exc2:  # noqa: BLE001
                HEALTH["stockbit"]["last_error"] = f"{sym}: {exc} | fallback: {exc2}"
                stats["errors"] += 1
                time.sleep(1)
                continue
        stats["fetched"] += len(posts)
        ids = [p.get("postid") for p in posts if p.get("postid")]
        seen_this_round.extend(ids)
        with conn() as c:
            for p in posts:
                pid = p.get("postid")
                if not pid:
                    continue
                alias_hits = resolve_tickers(p.get("content_original") or "")  # P3
                ts_src = p.get("created")
                dt = parse_src_ts(ts_src)
                cur = c.execute(
                    "INSERT OR IGNORE INTO stream_posts "
                    "(postid, text_original, created_at_src, created_at_utc, likes, replies, dislikes,"
                    " author_hash, flags, raw_payload, collected_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        pid,
                        p.get("content_original") or p.get("content") or p.get("title") or "",
                        ts_src,
                        iso(dt) if dt else None,
                        int(p.get("likes") or 0),
                        int(float(p.get("replies") or 0)),
                        int(p.get("dislikes") or 0),
                        author_hash(str(p.get("username") or p.get("userid") or "")),
                        json.dumps({
                            "official": bool(p.get("official")),
                            "isreport": bool(p.get("isreport")),
                            "isnews": bool(p.get("isnews")),
                        }),
                        json.dumps(sanitize_post(p), ensure_ascii=False, default=str)[:8000],  # P2: tanpa PII
                        iso(now_utc()),
                    ),
                )
                if cur.rowcount:
                    stats["new"] += 1
                    label, score, _hits = sentiment_score(p.get("content_original") or "")
                    c.execute(
                        "INSERT OR REPLACE INTO sentiment_predictions "
                        "(postid, model_version, sentiment, intent, score, confidence, processed_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (pid, "lexicon-demo-v1", label, "watch", score, 0.5, iso(now_utc())),
                    )
                    try:  # resolve arah semantic buat NEUTRAL (sidecar mati -> fallback pass-through)
                        sys.path.insert(0, BASE_DIR)
                        from ml.search.chatter_direction import resolve_direction
                    except Exception:  # noqa: BLE001
                        def resolve_direction(_pid, _t, _txt, lab, sc):
                            return lab, sc, "lexicon-v3"
                    # v3: skor per-cashtag (post multi-ticker gak lagi "sama rata").
                    # NEUTRAL per-ticker di-resolve arahnya pake bukti semantic
                    # kNN (sim>=0.75, k=3 wajib sepakat) -> label jadi
                    # bullish/bearish via model_version=semantic-knn-v1, jadi
                    # ikut terhitung di agregat. Sidecar mati -> tetap neutral.
                    joined = dict.fromkeys([sym] + [x for x in (p.get("topics") or []) if isinstance(x, str)]
                                           + [t for t, c, _ in alias_hits if c >= ENTITY_CONF_RELATION])
                    for t in joined:
                        if not re.fullmatch(r"[A-Z]{4}", str(t)):
                            continue
                        v2 = sentiment_for_ticker(p.get("content_original") or "", str(t))
                        if v2 is None:
                            v2 = (label, score)  # fallback whole-post
                        s_label, s_score, s_model = resolve_direction(
                            pid, str(t), p.get("content_original") or "", v2[0], v2[1])
                        c.execute(
                            "INSERT OR REPLACE INTO sentiment_per_ticker "
                            "(postid, ticker, model_version, sentiment, score, processed_at) "
                            "VALUES (?,?,?,?,?,?)",
                            (pid, t, s_model, s_label, s_score, iso(now_utc())),
                        )
                for t in dict.fromkeys([sym] + [x for x in (p.get("topics") or []) if isinstance(x, str)]):
                    if re.fullmatch(r"[A-Z]{4}", str(t)):
                        c.execute(
                            "INSERT OR IGNORE INTO stream_post_tickers (postid, ticker, relation_source, confidence) "
                            "VALUES (?,?,?,?)",
                            (pid, t, "server_topic", 0.9),
                        )
                for t, conf, _via in alias_hits:  # P3: relasi dari nama emiten
                    if conf >= ENTITY_CONF_RELATION:
                        c.execute(
                            "INSERT OR IGNORE INTO stream_post_tickers (postid, ticker, relation_source, confidence) "
                            "VALUES (?,?,?,?)",
                            (pid, t, "company_name", conf),
                        )
        time.sleep(1.5)  # stagger between tickers

    if seen_this_round:
        stats["overlap"] = round(len(prev_ids & set(seen_this_round)) / max(1, len(set(seen_this_round))), 2)
    # retensi 14 hari (plan bundle 02): post lebih tua dari 14d dibuang
    cutoff = iso(now_utc() - timedelta(days=14))
    with conn() as c:
        del_posts = c.execute(
            "DELETE FROM stream_posts WHERE created_at_utc IS NOT NULL AND created_at_utc < ?",
            (cutoff,),
        ).rowcount
    if del_posts:
        stats["pruned_old"] = del_posts
    HEALTH["stockbit"].update({
        "status": "ok" if stats["errors"] < len(TICKERS) else "down",
        "last_ok": iso(now_utc()) if stats["errors"] < len(TICKERS) else HEALTH["stockbit"]["last_ok"],
        "posts": stats["fetched"], "new": stats["new"], "overlap": stats["overlap"],
    })
    return stats


def preload_samples() -> int:
    """Preload the 62 anonymized posts from the feasibility PoC if available."""
    candidates = [
        os.path.join(BASE_DIR, "..", "stockbit_feasibility", "samples", "anonymized_posts.json"),
        os.path.join(BASE_DIR, "samples", "anonymized_posts.json"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if not path:
        return 0
    try:
        posts = json.load(open(path, encoding="utf-8"))
    except Exception:
        return 0
    inserted = 0
    with conn() as c:
        for p in posts:
            pid = p.get("source_post_id")
            if not pid:
                continue
            dt = parse_src_ts(p.get("published_at"))
            cur = c.execute(
                "INSERT OR IGNORE INTO stream_posts "
                "(postid, text_original, created_at_src, created_at_utc, likes, replies, dislikes,"
                " author_hash, flags, raw_payload, collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid, p.get("text") or "", p.get("published_at"),
                    iso(dt) if dt else None,
                    int((p.get("engagement") or {}).get("likes") or 0),
                    int((p.get("engagement") or {}).get("replies") or 0),
                    0, p.get("author_id"),
                    json.dumps(p.get("flags") or {}),
                    json.dumps(p, ensure_ascii=False)[:6000], iso(now_utc()),
                ),
            )
            if cur.rowcount:
                inserted += 1
                label, score, _ = sentiment_score(p.get("text") or "")
                c.execute(
                    "INSERT OR REPLACE INTO sentiment_predictions "
                    "(postid, model_version, sentiment, intent, score, confidence, processed_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (pid, "lexicon-demo-v1", label, "watch", score, 0.5, iso(now_utc())),
                )
                for t in p.get("ticker_candidates") or []:
                    if re.fullmatch(r"[A-Z]{4}", str(t)):
                        c.execute(
                            "INSERT OR IGNORE INTO stream_post_tickers (postid, ticker, relation_source, confidence) "
                            "VALUES (?,?,?,?)",
                            (pid, t, "server_topic", 0.9),
                        )
    return inserted


# --------------------------------------------------------------------------
# Aggregator (bundle 02 section 4 metrics)
# --------------------------------------------------------------------------
def aggregate(window_hours: int) -> list[dict]:
    end = now_utc()
    start = end - timedelta(hours=window_hours)
    prev_start = start - timedelta(hours=window_hours)

    out = []
    with conn() as c:
        for t in TICKERS:
            rows = c.execute(
                """
                SELECT sp.postid, sp.author_hash, sp.likes, sp.replies, sp.flags,
                       sp.text_original, sp.created_at_utc,
                       COALESCE(spv.sentiment, sen.sentiment) AS sentiment,
                       COALESCE(spv.score, sen.score) AS score,
                       CASE WHEN spv.postid IS NOT NULL THEN spv.model_version ELSE 'lexicon-demo-v1' END AS model_used
                FROM stream_post_tickers spt
                JOIN stream_posts sp ON sp.postid = spt.postid
                LEFT JOIN sentiment_per_ticker spv
                       ON spv.postid = sp.postid AND spv.ticker = spt.ticker
                      AND spv.model_version = 'lexicon-v3'
                LEFT JOIN sentiment_predictions sen
                       ON sen.postid = sp.postid AND sen.model_version = 'lexicon-demo-v1'
                WHERE spt.ticker = ?
                """,
                (t,),
            ).fetchall()

            # --- noise filter (plan bundle 02: exclude non-retail sources) ---
            def is_noise(r) -> bool:
                try:
                    f = json.loads(r["flags"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    f = {}
                if f.get("official") or f.get("isreport") or f.get("isnews"):
                    return True  # akun resmi emiten / repost riset broker / berita otomatis
                txt = (r["text_original"] or "").strip()
                if len(txt) < 12 or not re.search(r"[a-zA-Z]", txt):
                    return True  # terlalu pendek / cuma emoji-angka-link, gak ada opini
                return False

            noise_rows = [r for r in rows if is_noise(r)]
            rows = [r for r in rows if not is_noise(r)]
            cur = [r for r in rows if r["created_at_utc"] and start.isoformat() <= r["created_at_utc"] <= end.isoformat()]
            prev = [r for r in rows if r["created_at_utc"] and prev_start.isoformat() <= r["created_at_utc"] < start.isoformat()]

            def summarize(rs):
                bull = sum(1 for r in rs if (r["sentiment"] or "neutral") == "bullish")
                bear = sum(1 for r in rs if (r["sentiment"] or "neutral") == "bearish")
                neu = len(rs) - bull - bear
                net = round(100 * (bull - bear) / max(1, bull + bear + neu))
                authors = len({r["author_hash"] for r in rs if r["author_hash"]})
                eng = sum((1 + (r["likes"] or 0) + (r["replies"] or 0)) for r in rs)
                return {
                    "mention_count": len(rs), "bullish": bull, "bearish": bear, "neutral": neu,
                    "net_sentiment": net, "unique_authors": authors, "engagement_weighted": eng,
                }

            a = summarize(cur)
            p = summarize(prev)
            # velocity cuma reliable kalau prev window cukup terisi (store muda
            # tanpa backfill -> prev sering 1-2 post, persen jadi ngawur)
            a["prev_mention_count"] = p["mention_count"]
            a["mention_velocity"] = (
                round(100 * (a["mention_count"] - p["mention_count"]) / p["mention_count"])
                if p["mention_count"] >= 3 and a["mention_count"] else None
            )
            a["sample_ok"] = a["mention_count"] >= 5
            a["ticker"] = t
            a["noise_filtered"] = len(noise_rows)
            a["latest_post_at"] = max((r["created_at_utc"] for r in cur if r["created_at_utc"]), default=None)
            a["window_hours"] = window_hours
            out.append(a)
    return out


# Sektor tematik watchlist demo (bukan klasifikasi resmi IDX board)
TICKER_SECTOR = {
    "BBCA": "Bank", "BBRI": "Bank", "BMRI": "Bank",
    "TLKM": "Telko",
    "ANTM": "Logam & Mineral", "INCO": "Logam & Mineral",
    "ADRO": "Energi", "BUMI": "Energi", "UNTR": "Energi",
    "GOTO": "Teknologi",
}


# --------------------------------------------------------------------------
# Breaking news rule engine (bundle 03/04, ported)
# --------------------------------------------------------------------------
def clean(v: str | None) -> str:
    return SPACE_RE.sub(" ", TAG_RE.sub(" ", unescape(v or ""))).strip()


def norm(v: str) -> str:
    v = clean(v).lower()
    v = re.sub(r"https?://\S+", " ", v)
    v = re.sub(r"[^a-z0-9$ ]+", " ", v)
    return SPACE_RE.sub(" ", v).strip()


def denial_hits(ntext: str) -> list[str]:
    """P3.5: frasa bantahan/negasi material di judul (hard negatives dok V2 13.2)."""
    padded = f" {ntext} "
    return [p for p in DENIAL_PHRASES if f" {p} " in padded]


def lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node: ET.Element, names: set[str]) -> str:
    for ch in list(node):
        if lname(ch.tag) in names:
            return clean("".join(ch.itertext()))
    return ""


def child_link(node: ET.Element) -> str:
    for ch in list(node):
        if lname(ch.tag) == "link":
            text = clean(ch.text or "")
            if text.startswith("http"):
                return text
            href = ch.attrib.get("href", "")
            if href.startswith("http"):
                return href
    return ""


def detect_entities(text: str, mapping: dict[str, list[str]]) -> list[str]:
    ntext = f" {norm(text)} "
    found = []
    for entity, aliases in mapping.items():
        for alias in aliases:
            a = norm(alias)
            if f" {a} " in ntext or f"${a}" in ntext:
                found.append(entity)
                break
    return sorted(set(found))


def resolve_tickers(text: str) -> list[tuple[str, float, str]]:
    """P3: resolver emiten -> [(ticker, confidence, via)].
    Cashtag $TICKER = 1.0; nama/alias emiten = confidence per kamus (max yang match).
    Word-boundary matching di teks ternormalisasi (aman dari substring)."""
    ntext = f" {norm(text)} "
    hits = []
    for t in TICKERS:
        if f" ${t.lower()} " in ntext:
            hits.append((t, 1.0, "cashtag"))
            continue
        best = None
        for alias, conf in TICKER_ALIASES.get(t, {}).items():
            if f" {alias} " in ntext and (best is None or conf > best[1]):
                best = (t, conf, alias)
        if best:
            hits.append(best)
    return hits


def recency_points(age_min: float) -> int:
    if age_min <= 5:
        return 25
    if age_min <= 15:
        return 20
    if age_min <= 30:
        return 14
    if age_min <= 60:
        return 8
    if age_min <= 120:
        return 3
    return 0


def rule_score(published: datetime, priority: float, tickers: list[str],
               sectors: list[str], keywords: dict[str, int]) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    age = max(0.0, (now_utc() - published).total_seconds() / 60)
    rp = recency_points(age)
    score += rp
    reasons.append(f"recency +{rp}")
    sp = round(10 * priority, 1)
    score += sp
    reasons.append(f"source +{sp}")
    if tickers:
        score += 15
        reasons.append(f"direct ticker +15 ({','.join(tickers)})")
    if sectors:
        pts = min(8, 4 + len(sectors) * 2)
        score += pts
        reasons.append(f"sector +{pts}")
    if keywords:
        pts = min(30, max(keywords.values()))
        score += pts
        reasons.append(f"material keyword +{pts}")
    if bool(set(keywords) & HARD_EVENTS) and tickers:
        score += 12
        reasons.append("hard-event +12")
    return round(min(100.0, score), 1), reasons


def derive_severity(score: float, confidence: float) -> str:
    composite = 0.45 * score + 55 * confidence
    if composite >= 80:
        return "CRITICAL"
    if composite >= 65:
        return "HIGH"
    if composite >= 50:
        return "MEDIUM"
    return "INFO"


def fetch_url(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MarketForgeDemo/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_stockbit_fallback(sym: str, timeout: int = 25) -> list[dict]:
    """Fallback dari riset feasibility: halaman SSR stockbit.com/symbol/{SYM}
    embed 30 post yang sama di __NEXT_DATA__ (schema identik dgn API non-login)."""
    html = fetch_url(f"https://stockbit.com/symbol/{sym}", timeout=timeout).decode("utf-8", "ignore")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("fallback: __NEXT_DATA__ tidak ditemukan")
    posts = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("posts")
    if not isinstance(posts, list):
        raise ValueError("fallback: struktur posts tidak dikenali")
    return posts


def breaking_once() -> dict:
    """Poll RSS sources, score items, persist candidates, trigger stale flow."""
    stats = {"sources_ok": 0, "candidates": 0, "triggered": 0, "errors": [], "denial_suppressed": 0}
    for src in RSS_SOURCES:
        try:
            xml_bytes = fetch_url(src["url"])
            root = ET.fromstring(xml_bytes)
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"{src['name']}: {exc}")
            continue
        stats["sources_ok"] += 1
        for node in root.iter():
            if lname(node.tag) not in {"item", "entry"}:
                continue
            title = clean_text(child_text(node, {"title"}), 300)  # P2: strip tag/entity
            if not title:
                continue
            summary = clean_text(child_text(node, {"description", "summary", "content", "encoded"}), 1200)
            link = child_link(node)
            pub_raw = child_text(node, {"pubdate", "published", "updated", "date"})
            try:
                published = parsedate_to_datetime(pub_raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                published = published.astimezone(timezone.utc)
            except Exception:
                published = now_utc()
            full_text = f"{title} {summary}"

            # --- kategorisasi ala News Preview (8503): Dividen / Lainnya ---
            is_div = "dividen" in norm(full_text)
            category = "Dividen" if is_div else "Lainnya"

            # --- skor & entity detection untuk SEMUA artikel (tanpa cut age) ---
            resolved = resolve_tickers(full_text)  # P3: cashtag + alias emiten
            tickers_all = sorted({t for t, _, _ in resolved})
            tickers = sorted({t for t, c, _ in resolved if c >= ENTITY_CONF_HIGH})
            sectors = detect_entities(full_text, SECTOR_ALIASES)
            ntext = norm(full_text)
            keywords = {k: v for k, v in IMPACT_KEYWORDS.items() if norm(k) in ntext}
            denial = denial_hits(ntext)  # P3.5
            if denial and keywords:
                keywords = {}
                stats["denial_suppressed"] += 1
            score, reasons = rule_score(published, float(src["priority"]), tickers, sectors, keywords)
            score = min(score, 100.0)
            # semantic materiality (Cohere embed-v4.0): kedekatan makna artikel ke arketipe
            # material vs non-material. Default PENDUKUNG (chip SEM; trigger
            # tetap rule). By-context: kalau rule-nya "hampir" (>= ESCALATE_FLOOR
            # tapi di bawah threshold) DAN semantic yakin material
            # (margin >= ESCALATE_MARGIN, kalibrasi 40 pilot) -> skor dinaikin
            # sedikit ke atas threshold supaya ikut jadi kandidat event.
            # Guard: keyword material harus ada + gak sedang denial; sidecar
            # mati -> None, pipeline jalan normal.
            try:
                sys.path.insert(0, BASE_DIR)
                from ml.search.news_semantic import materiality_margin
                sem_margin = materiality_margin(title, summary)
            except Exception:  # noqa: BLE001
                sem_margin = None
            sem_label = ("material" if (sem_margin or 0) >= 0.20 else "non_material") \
                if sem_margin is not None else None
            sem_escalated = False
            if (sem_margin is not None and CANDIDATE_THRESHOLD > score >= ESCALATE_FLOOR
                    and sem_margin >= ESCALATE_MARGIN and keywords and not denial):
                score = round(min(100.0, CANDIDATE_THRESHOLD + 1.0), 1)
                sem_escalated = True
                stats["sem_escalated"] = stats.get("sem_escalated", 0) + 1
                reasons.append(
                    f"semantic eskalasi: margin {sem_margin:+.2f} material by-context "
                    f"(rule {ESCALATE_FLOOR:.0f}-{CANDIDATE_THRESHOLD:.0f})")
            if denial:  # P3.5: dicatat di audit trail, bukan di skor
                reasons.append(f"denial guard: {', '.join(denial)} -> keyword material disuppress")
            if tickers_all != tickers:  # P3: transparansi tiering entity
                med = sorted(set(tickers_all) - set(tickers))
                reasons.append(f"entity tiering: {', '.join(med) or '-'} = confidence rendah (tanpa bonus skor)")

            # --- simpan semua artikel (window 48 jam) biar view News ramai ---
            url_hash = hashlib.sha1((link or title).encode()).hexdigest()
            with conn() as c:
                c.execute(
                    "INSERT OR IGNORE INTO news_articles "
                    "(url_hash, source_name, source_url, title, summary, published_at, fetched_at,"
                    " rule_score, tickers, sectors, keywords, category, sem_margin, sem_label) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        url_hash, src["name"], link, title, (summary or "")[:400],
                        iso(published), iso(now_utc()), score,
                        json.dumps(tickers_all), json.dumps(sectors), json.dumps(keywords),
                        category,
                        sem_margin, sem_label,
                    ),
                )

            # --- jalur breaking event (hanya item segar + lolos threshold) ---
            age_min = (now_utc() - published).total_seconds() / 60
            if age_min > MAX_ITEM_AGE_MIN:
                continue
            if score < CANDIDATE_THRESHOLD:
                continue

            ehash = hashlib.sha256(f"{norm(title)}|{link}".encode()).hexdigest()
            # Validator proxy (demo): confidence scales with score;
            # eskalasi semantic dapet diskon kecil (bukan keputusan rule murni).
            confidence = round(min(0.9, 0.5 + score * 0.004) - (0.05 if sem_escalated else 0.0), 2)
            severity = derive_severity(score, confidence)
            status = "CANDIDATE"
            if tickers and severity in ("HIGH", "CRITICAL"):
                status = "TRIGGERED"
                stats["triggered"] += 1
            stats["candidates"] += 1

            with conn() as c:
                inserted = c.execute(
                    "INSERT OR IGNORE INTO breaking_events "
                    "(event_hash, source_name, source_url, headline, published_at, detected_at,"
                    " rule_score, reasons, matched_keywords, tickers, sectors, confidence, severity, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ehash, src["name"], link, title, iso(published), iso(now_utc()),
                        score, json.dumps(reasons), json.dumps(keywords),
                        json.dumps(tickers_all), json.dumps(sectors), confidence, severity, status,
                    ),
                ).rowcount
                # P0 idempotency: stale flag + timer reassess HANYA saat event benar2 baru,
                # bukan tiap kali artikel yang sama keluar lagi di poll berikutnya
                if status == "TRIGGERED" and inserted == 1:
                    for t in tickers:
                        c.execute(
                            "UPDATE recommendations SET stale=1, stale_reason='material_event',"
                            " stale_event_hash=?, stale_at=?, reassessed_at=NULL, reassessed_note=NULL "
                            "WHERE ticker=?",
                            (ehash, iso(now_utc()), t),
                        )
                    if REASSESS_DELAY > 0:
                        threading.Timer(
                            REASSESS_DELAY, simulate_reassessment, args=(ehash, tickers)
                        ).start()
    # P0: retensi artikel 48 jam — news_articles gak bengkak.
    # IndoBERT plan (Phase B): artikel yang mau di-prune di-archive dulu ke JSONL
    # biar stok data buat labeling materiality gak nguap.
    cutoff_news = iso(now_utc() - timedelta(hours=NEWS_RETENTION_H))
    with conn() as c:
        dying = [dict(r) for r in c.execute(
            "SELECT * FROM news_articles WHERE published_at IS NOT NULL AND published_at < ?",
            (cutoff_news,),
        ).fetchall()]
    if dying:
        arch = os.path.join(BASE_DIR, "data", "news", "archive_news.jsonl")
        os.makedirs(os.path.dirname(arch), exist_ok=True)
        with open(arch, "a", encoding="utf-8") as f:
            for a in dying:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        stats["archived_news"] = len(dying)
    with conn() as c:
        del_news = c.execute(
            "DELETE FROM news_articles WHERE published_at IS NOT NULL AND published_at < ?",
            (cutoff_news,),
        ).rowcount
    if del_news:
        stats["pruned_news"] = del_news
    HEALTH["rss"].update({
        "status": "ok" if stats["sources_ok"] else "down",
        "last_ok": iso(now_utc()) if stats["sources_ok"] else HEALTH["rss"]["last_ok"],
        "last_error": "; ".join(stats["errors"]) or None,
        "sources_ok": stats["sources_ok"], "candidates": stats["candidates"],
    })
    return stats


def simulate_reassessment(ehash: str, tickers: list[str]) -> None:
    """Simulated targeted agent rerun (analyst -> researcher -> risk-PM)."""
    with conn() as c:
        for t in tickers:
            row = c.execute("SELECT score FROM recommendations WHERE ticker=?", (t,)).fetchone()
            if not row:
                continue
            delta = (int(hashlib.md5((ehash + t).encode()).hexdigest(), 16) % 7) - 3
            new_score = max(1, min(99, row["score"] + delta))
            # P0: label ikut dihitung ulang bareng skor (dulu bisa skor 80 + label HOLD)
            new_label = "BUY" if new_score >= 75 else ("WATCH" if new_score >= 65 else "HOLD")
            c.execute(
                "UPDATE recommendations SET score=?, grade=?, label=?, stale=0,"
                " reassessed_at=?, reassessed_note=? WHERE ticker=?",
                (
                    new_score,
                    grade_for(new_score),
                    new_label,
                    iso(now_utc()),
                    f"simulated targeted reassessment (delta {delta:+d})",
                    t,
                ),
            )


def reset_db() -> None:
    """P0: reset LENGKAP — dulu sentiment_per_ticker & news_articles gak ikut kehapus (orphan)."""
    with conn() as c:
        c.executescript(
            "DELETE FROM stream_post_tickers; DELETE FROM sentiment_predictions;"
            " DELETE FROM sentiment_per_ticker; DELETE FROM news_articles;"
            " DELETE FROM stream_posts; DELETE FROM breaking_events;"
            " UPDATE recommendations SET stale=0, stale_reason=NULL, stale_event_hash=NULL,"
            " stale_at=NULL, reassessed_at=NULL, reassessed_note=NULL;"
        )


def grade_for(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def seed_recommendations() -> None:
    with conn() as c:
        for t in TICKERS:
            score = 55 + int(hashlib.md5(t.encode()).hexdigest(), 16) % 31  # 55..85 deterministic
            label = "BUY" if score >= 75 else ("WATCH" if score >= 65 else "HOLD")
            c.execute(
                "INSERT OR IGNORE INTO recommendations (ticker, score, grade, label, updated_at)"
                " VALUES (?,?,?,?,?)",
                (t, score, grade_for(score), label, iso(now_utc())),
            )


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def _ml_status() -> dict:
    """Status model IndoBERT opsional (IndoBERT plan bag. 25). Import-protected:
    demo stdlib tetap jalan penuh walau torch/transformers gak terpasang."""
    try:
        sys.path.insert(0, BASE_DIR)
        from ml import common
        return {
            "available": common.AVAILABLE,
            "sentiment": common.status(common.os.path.join(common.MODELS_DIR, "indobert-stockbit-sentiment-v1")),
            "materiality": common.status(common.os.path.join(common.MODELS_DIR, "indobert-news-materiality-v1")),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


def _search_mode() -> dict:
    """Status semantic search (import-protected, pola sama dengan _ml_status)."""
    try:
        sys.path.insert(0, BASE_DIR)
        from ml import search as _search_mod
        n = 0
        try:
            con = sqlite3.connect(DB_PATH)
            n = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            con.close()
        except Exception:  # noqa: BLE001 — tabel belum ada
            n = 0
        if _search_mod.sidecar_available() and n > 0:
            mode = "semantic (Cohere embed-v4.0 via API)"
        elif n > 0:
            mode = "keyword fallback (COHERE_API_KEY gak ada / API gagal, index tersedia)"
        else:
            mode = "keyword fallback (index kosong — jalankan scripts/build_embeddings_cohere.py)"
        return {"mode": mode, "indexed": n}
    except Exception as exc:  # noqa: BLE001
        return {"mode": "keyword fallback (modul search tidak tersedia)", "error": str(exc)}


def build_state() -> dict:
    with conn() as c:
        recs = [dict(r) for r in c.execute("SELECT * FROM recommendations ORDER BY score DESC")]
        events = [dict(r) for r in c.execute(
            "SELECT * FROM breaking_events ORDER BY detected_at DESC LIMIT 25")]
        total_posts = c.execute("SELECT COUNT(*) n FROM stream_posts").fetchone()["n"]
        total_sen = c.execute("SELECT COUNT(*) n FROM sentiment_predictions").fetchone()["n"]
    for r in recs:
        r["stale"] = bool(r["stale"])
    for e in events:
        e["reasons"] = json.loads(e["reasons"] or "[]")
        e["tickers"] = json.loads(e["tickers"] or "[]")
        e["sectors"] = json.loads(e["sectors"] or "[]")
        e["matched_keywords"] = json.loads(e["matched_keywords"] or "{}")
    with conn() as c:
        articles = [dict(r) for r in c.execute(
            "SELECT url_hash, source_name, source_url, title, summary, published_at,"
            " rule_score, tickers, category, sem_margin, sem_label FROM news_articles "
            "ORDER BY published_at DESC LIMIT 40")]
        cat_counts = [dict(r) for r in c.execute(
            "SELECT category, COUNT(*) n FROM news_articles GROUP BY category")]
        art_stats = {
            "fetched": c.execute("SELECT COUNT(*) n FROM news_articles").fetchone()["n"],
            "dividen": next((r["n"] for r in cat_counts if r["category"] == "Dividen"), 0),
            "lainnya": next((r["n"] for r in cat_counts if r["category"] == "Lainnya"), 0),
        }
    for a in articles:
        a["tickers"] = json.loads(a["tickers"] or "[]")
    t1, t24 = aggregate(1), aggregate(24)
    # rollup sektor dari agregat 24 jam (tematik, sesuai TICKER_SECTOR)
    roll: dict[str, dict] = {}
    for a in t24:
        sec = TICKER_SECTOR.get(a["ticker"], "Lainnya")
        d = roll.setdefault(sec, {"sectors": sec, "mention_count": 0, "bullish": 0, "bearish": 0, "tickers": []})
        d["mention_count"] += a["mention_count"]
        d["bullish"] += a["bullish"]
        d["bearish"] += a["bearish"]
        d["tickers"].append(a["ticker"])
    for d in roll.values():
        d["net_sentiment"] = round(100 * (d["bullish"] - d["bearish"]) / max(1, d["mention_count"]))
        d["tickers"] = ", ".join(sorted(d["tickers"]))
    sector_rollup = sorted(roll.values(), key=lambda d: -d["mention_count"])
    return {
        "generated_at": iso(now_utc()),
        "mode": {
            "sentiment_model": "lexicon-v3 per-cashtag + context rules; post per-ticker NEUTRAL di-resolve arah semantic kNN (sim>=0.75, k=3 sepakat, presisi ~60% di pilot 150) via model_version=semantic-knn-v1 (audit: tabel semantic_direction_log)",
            "entity_resolution": "cashtag + alias nama emiten (confidence; >=0.8 dapat bonus skor)",
            "ml_models": _ml_status(),
            "search": _search_mode(),
            "validator": "rule-only proxy (LLM validator tidak aktif di demo)",
            "recommendations": "DEMO baseline dummy, bukan sinyal riil",
        },
        "tickers_1h": t1,
        "tickers_24h": t24,
        "sector_rollup": sector_rollup,
        "events": events,
        "articles": articles,
        "articles_stats": art_stats,
        "recommendations": recs,
        "health": {
            "started_at": HEALTH["started_at"],
            "posts_in_store": total_posts,
            "posts_sentiment_scored": total_sen,
            "stockbit": {k: HEALTH["stockbit"][k] for k in ("status", "last_ok", "last_error", "posts", "new", "overlap")},
            "rss": {k: HEALTH["rss"][k] for k in ("status", "last_ok", "last_error", "sources_ok", "candidates")},
            "watchlist": TICKERS,
        },
    }


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "MarketForgeDemo/1.0"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _static(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            fp = os.path.join(STATIC_DIR, "index.html")
        else:
            safe = os.path.normpath(path).replace("\\", "/").lstrip("/")
            fp = os.path.join(STATIC_DIR, safe)
            if not os.path.abspath(fp).startswith(os.path.abspath(STATIC_DIR)):
                self._json({"error": "forbidden"}, 403)
                return
        if not os.path.exists(fp):
            self._json({"error": "not found"}, 404)
            return
        ctype = "text/html; charset=utf-8" if fp.endswith(".html") else (
            "text/css; charset=utf-8" if fp.endswith(".css") else (
                "application/javascript; charset=utf-8" if fp.endswith(".js") else "application/octet-stream"
            )
        )
        body = open(fp, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html") or path.startswith("/static/") or path.endswith(".css") or path.endswith(".js"):
                self._static()
            elif path == "/api/state":
                self._json(build_state())
            elif path == "/api/events":
                with conn() as c:
                    rows = [dict(r) for r in c.execute(
                        "SELECT * FROM breaking_events ORDER BY detected_at DESC LIMIT 50")]
                for e in rows:
                    e["reasons"] = json.loads(e["reasons"] or "[]")
                    e["tickers"] = json.loads(e["tickers"] or "[]")
                self._json({"events": rows})
            elif path.startswith("/api/event/"):
                ehash = path.split("/api/event/", 1)[1]
                with conn() as c:
                    row = c.execute("SELECT * FROM breaking_events WHERE event_hash=?", (ehash,)).fetchone()
                    if not row:
                        self._json({"error": "not found"}, 404)
                        return
                    ev = dict(row)
                    ev["reasons"] = json.loads(ev["reasons"] or "[]")
                    ev["tickers"] = json.loads(ev["tickers"] or "[]")
                    ev["sectors"] = json.loads(ev["sectors"] or "[]")
                    ev["matched_keywords"] = json.loads(ev["matched_keywords"] or "{}")
                    affected = c.execute(
                        "SELECT * FROM recommendations WHERE stale_event_hash=?", (ehash,)
                    ).fetchall()
                    ev["stale_recommendations"] = [dict(r) for r in affected]
                self._json({"event": ev})
            elif path == "/api/health":
                self._json(build_state()["health"])
            elif path == "/api/search":
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                q = (params.get("q", [""])[0] or "").strip()
                if not q:
                    self._json({"error": "parameter q wajib"}, 400)
                    return
                kinds_raw = params.get("kinds", [None])[0]
                kinds = [k.strip() for k in kinds_raw.split(",") if k.strip()] if kinds_raw else None
                try:
                    limit = min(max(int(params.get("limit", ["20"])[0]), 1), 50)
                except ValueError:
                    limit = 20
                try:
                    sys.path.insert(0, BASE_DIR)
                    from ml.search import search as _search_fn
                    self._json(_search_fn(q, top_k=limit, kinds=kinds))
                except Exception as exc:  # noqa: BLE001
                    self._json({"error": f"search gagal: {exc}"}, 500)
            elif path == "/api/chatter":
                tkr_req = self.path.rsplit("ticker=", 1)[-1].split("&")[0].upper()
                if not re.fullmatch(r"[A-Z]{4}", tkr_req):  # P2: input validation
                    self._json({"error": "ticker tidak valid"}, 400)
                    return
                with conn() as c:
                    rows = [dict(r) for r in c.execute(
                        "SELECT sp.postid, sp.text_original AS text, sp.created_at_utc AS created_at, "
                        "sp.likes, sp.replies, sp.flags, "
                        "COALESCE(spv.sentiment, sen.sentiment) AS sentiment "
                        "FROM stream_posts sp "
                        "JOIN stream_post_tickers spt ON spt.postid = sp.postid "
                        "LEFT JOIN sentiment_per_ticker spv "
                        "  ON spv.postid = sp.postid AND spv.ticker = spt.ticker "
                        " AND spv.model_version = 'lexicon-v3' "
                        "LEFT JOIN sentiment_predictions sen "
                        "  ON sen.postid = sp.postid AND sen.model_version = 'lexicon-demo-v1' "
                        "WHERE spt.ticker = ? AND sp.created_at_utc IS NOT NULL "
                        "ORDER BY sp.created_at_utc DESC LIMIT 25",
                        (tkr_req,),
                    )]
                out = []
                for p in rows:
                    try:
                        f = json.loads(p.pop("flags") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        f = {}
                    txt = (p["text"] or "").strip()
                    if f.get("official") or f.get("isreport") or f.get("isnews") or len(txt) < 12:
                        continue  # sama seperti filter noise di tabel sentiment
                    p["text"] = txt[:240]
                    pid = p.pop("postid")
                    p["url"] = f"https://stockbit.com/post/{pid}"
                    p["author"] = "sb:" + hashlib.sha1(str(pid).encode()).hexdigest()[:8]
                    out.append(p)
                    if len(out) >= 12:
                        break
                # Semantic direction hint (PENDUKUNG): buat post neutral,
                # cocokin ke pool post berlabel jelas (cosine >= 0.75, k=3,
                # wajib sepakat). Sidecar mati -> tanpa hint, chatter jalan normal.
                try:
                    sys.path.insert(0, BASE_DIR)
                    from ml.search.chatter_direction import semantic_direction
                    for p in out:
                        if (p.get("sentiment") or "neutral") == "neutral":
                            hint = semantic_direction(p.get("text") or "")
                            if hint:
                                p["semantic_hint"] = hint
                except Exception:  # noqa: BLE001 — hint bersifat opsional
                    pass
                self._json({"posts": out})
            else:
                self._json({"error": "unknown endpoint"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 500)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path == "/api/poll":
                s = collect_once()
                b = breaking_once()
                self._json({"collector": s, "breaking": b})
            elif path == "/api/reset":
                reset_db()
                seed_recommendations()
                self._json({"ok": True})
            elif path == "/api/simulate_event":
                # inject a synthetic material event for demo when RSS is quiet
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length > 0 else b""
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    body = {}
                ticker = (body.get("ticker") or "ANTM").upper()
                if ticker not in TICKERS:
                    self._json({"error": "ticker not in watchlist"}, 400)
                    return
                headline = f"[SIMULASI] {ticker} menghentikan sementara operasi utama pabrik"
                ehash = hashlib.sha256(f"{norm(headline)}|demo-sim".encode()).hexdigest()
                score, reasons = rule_score(now_utc() - timedelta(minutes=2), 1.0, [ticker], ["Basic Materials"], {"produksi dihentikan": 24, "suspensi": 28})
                confidence = round(min(0.9, 0.5 + score * 0.004), 2)
                severity = derive_severity(score, confidence)
                with conn() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO breaking_events "
                        "(event_hash, source_name, source_url, headline, published_at, detected_at,"
                        " rule_score, reasons, matched_keywords, tickers, sectors, confidence, severity, status) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ehash, "DEMO SIMULATOR", "", headline, iso(now_utc() - timedelta(minutes=2)),
                         iso(now_utc()), score, json.dumps(reasons + ["simulated event"]),
                         json.dumps({"produksi dihentikan": 24, "suspensi": 28}),
                         json.dumps([ticker]), json.dumps(["Basic Materials"]),
                         confidence, severity, "TRIGGERED"),
                    )
                    c.execute(
                        "UPDATE recommendations SET stale=1, stale_reason='material_event',"
                        " stale_event_hash=?, stale_at=?, reassessed_at=NULL, reassessed_note=NULL WHERE ticker=?",
                        (ehash, iso(now_utc()), ticker),
                    )
                threading.Timer(REASSESS_DELAY, simulate_reassessment, args=(ehash, [ticker])).start()
                self._json({"ok": True, "event_hash": ehash, "score": score, "severity": severity})
            else:
                self._json({"error": "unknown endpoint"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 500)


# --------------------------------------------------------------------------
# Background workers
# --------------------------------------------------------------------------
def collector_loop() -> None:
    while True:
        try:
            collect_once()
        except Exception as exc:  # noqa: BLE001
            HEALTH["stockbit"]["last_error"] = str(exc)
        time.sleep(max(60, COLLECT_INTERVAL))


def breaking_loop() -> None:
    while True:
        try:
            breaking_once()
        except Exception as exc:  # noqa: BLE001
            HEALTH["rss"]["last_error"] = str(exc)
        time.sleep(max(30, BREAKING_INTERVAL))


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------
def selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + f"  {name}")
        ok = ok and bool(cond)

    label, score, hits = sentiment_score("$BBCA naik terus cuan parah gak rugi")
    check("sentiment bullish detect", label == "bullish" and score > 0)
    label, score, _ = sentiment_score("busuk banget $BUMI turun mulu panic sell")
    check("sentiment bearish detect", label == "bearish" and score < 0)
    label, _, _ = sentiment_score("jadwal rilis laporan keuangan minggu depan")
    check("sentiment neutral fallback", label == "neutral")
    # v2: per-cashtag window
    v2a = sentiment_for_ticker("$BUMI naik terus gacor, $BBCA anjlok jeblok panic", "BUMI")
    v2b = sentiment_for_ticker("$BUMI naik terus gacor, $BBCA anjlok jeblok panic", "BBCA")
    check("v2 per-cashtag BUMI bullish", v2a is not None and v2a[0] == "bullish" and v2a[1] > 0)
    check("v2 per-cashtag BBCA bearish", v2b is not None and v2b[0] == "bearish" and v2b[1] < 0)
    check("v2 slang 'anjloooq' kebaca", sentiment_score("$GOTO anjloooq parah")[0] == "bearish")
    check("v2 tanpa cashtag -> None", sentiment_for_ticker("naik terus cuan parah", "BBCA") is None)
    from ml.search.chatter_direction import resolve_direction
    check("resolve passthrough (bukan neutral)",
          resolve_direction(1, "BBCA", "apa aja", "bullish", 0.5) == ("bullish", 0.5, "lexicon-v3"))
    s, r = rule_score(now_utc() - timedelta(minutes=2), 1.0, ["ANTM"], ["Basic Materials"], {"produksi dihentikan": 24})
    check("rule score material > plain", s >= 45 and any("hard-event" in x for x in r))
    s_plain, _ = rule_score(now_utc() - timedelta(minutes=2), 1.0, ["ANTM"], [], {})
    check("material > plain", s > s_plain)
    check("severity derive", derive_severity(60, 0.74) == "HIGH")
    check("author hash stable", author_hash("a") == author_hash("a") and author_hash("a") != author_hash("b"))

    # ---- Phase 1 regression: correctness DB (pakai DB sementara, gak nyentuh demo.db) ----
    global DB_PATH
    import tempfile
    _db_old, DB_PATH = DB_PATH, os.path.join(tempfile.mkdtemp(prefix="mf_selftest_"), "t.db")
    try:
        init_db()
        with conn() as c:
            c.execute("INSERT OR IGNORE INTO breaking_events (event_hash, status) VALUES ('h1','TRIGGERED')")
            rc_dup = c.execute(
                "INSERT OR IGNORE INTO breaking_events (event_hash, status) VALUES ('h1','TRIGGERED')"
            ).rowcount
        check("P0 event idempotent (dup -> rowcount 0)", rc_dup == 0)
        try:
            with conn() as c:
                c.execute("INSERT INTO stream_post_tickers (postid, ticker) VALUES (987654, 'BBCA')")
            fk_ok = False
        except sqlite3.IntegrityError:
            fk_ok = True
        check("P0 foreign keys ON (orphan ditolak)", fk_ok)
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO recommendations (ticker, score, grade, label) VALUES ('TEST', 80, 'A', 'HOLD')")
        simulate_reassessment("h1", ["TEST"])
        with conn() as c:
            rr = c.execute("SELECT score, label FROM recommendations WHERE ticker='TEST'").fetchone()
        exp_label = "BUY" if rr["score"] >= 75 else ("WATCH" if rr["score"] >= 65 else "HOLD")
        check("P0 reassessment label ikut skor", rr["label"] == exp_label)
        reset_db()
        with conn() as c:
            leftover = c.execute(
                "SELECT (SELECT COUNT(*) FROM stream_posts) + (SELECT COUNT(*) FROM news_articles)"
                " + (SELECT COUNT(*) FROM sentiment_per_ticker) + (SELECT COUNT(*) FROM breaking_events)"
            ).fetchone()[0]
        check("P0 reset bersih semua tabel", leftover == 0)
    finally:
        DB_PATH = _db_old

    # ---- Phase 2 regression: security ----
    sp = sanitize_post({"postid": 1, "username": "budi", "avatar": "x.png", "likes": 3})
    check("P2 sanitize buang username/avatar", "username" not in sp and "avatar" not in sp and sp["likes"] == 3)
    check("P2 sanitize nested dict", "user" not in sanitize_post({"likes": 1, "user": {"username": "x"}}))
    check("P2 clean_text strip tag+entity", clean_text("<b>PT &amp; Anak</b> Cilegon<br>Rudi") == "PT & Anak Cilegon Rudi")
    check("P2 clean_text limit 300", len(clean_text("a" * 5000, 300)) == 300)

    # ---- P3.5 regression: context rules ----
    l, s, _ = sentiment_score("$BBCA gak rugi kok malah cuan")
    check("P3.5 intent-flip 'gak rugi' -> bullish", l == "bullish" and s > 0)
    check("P3.5 frasa 'gap down' kebaca", sentiment_score("$BBCA gap down parah")[0] == "bearish")
    check("P3.5 frasa 'back to mahkota' kebaca", sentiment_score("$BBCA back to mahkota gacor")[0] == "bullish")
    check("P3.5 'gak naik' -> bearish", sentiment_score("$GOTO gak naik gak")[0] == "bearish")
    check("P3.5 denial 'membantah isu pailit'", "membantah" in denial_hits(norm("perusahaan membantah isu pailit")))
    check("P3.5 denial 'belum dikonfirmasi'", "belum dikonfirmasi" in denial_hits(norm("rumor suspensi belum dikonfirmasi")))

    # ---- P3 regression: entity resolver ----
    r = resolve_tickers("antam tembus rekor produksi emas")
    check("P3 alias 'antam' -> ANTM high", any(t == "ANTM" and c >= 0.8 for t, c, _ in r))
    r = resolve_tickers("bank central asia catat laba melonjak")
    check("P3 'bank central asia' -> BBCA 0.95", any(t == "BBCA" and c >= 0.9 for t, c, _ in r))
    check("P3 substring aman ('bri' gak nyangkut)", resolve_tickers("perpustakaan nalibrinya") == [])
    r = resolve_tickers("$antm naik")
    check("P3 cashtag conf 1.0", bool(r) and r[0][0] == "ANTM" and r[0][1] == 1.0)
    check("P3 'telkomsel' -> TLKM", any(t == "TLKM" for t, _, _ in resolve_tickers("telkomsel perkuat jaringan")))
    check("P3 tiering 'bumi' 0.6 gak high", [t for t, c, _ in resolve_tickers("bumi terlihat indah") if c >= 0.8] == [])
    check("P3 'gojek' -> GOTO high", any(t == "GOTO" and c >= 0.8 for t, c, _ in resolve_tickers("gojek garap fitur baru")))

    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-rss", action="store_true", help="skip breaking-news RSS polling")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    init_db()
    seed_recommendations()
    preloaded = preload_samples()
    HEALTH["started_at"] = iso(now_utc())

    if not args.no_rss:
        threading.Thread(target=breaking_loop, daemon=True).start()
    # P0: SATU collector cukup — dulu ada 2 thread nyangkut, poll dobel saat startup
    threading.Thread(target=collector_loop, daemon=True).start()

    httpd = None
    port = PORT
    for attempt in range(6):
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", port), ApiHandler)
            break
        except OSError:
            print(f"[WARN] port {port} busy, trying {port + 1}")
            port += 1
    if httpd is None:
        print("[ERROR] no free port found")
        return 1

    print("=" * 62)
    print(" MarketForge Demo - Sentiment + Breaking Event (standalone)")
    print(f" URL        : http://127.0.0.1:{port}/")
    print(f" DB         : {DB_PATH}")
    print(f" Preloaded  : {preloaded} posts dari feasibility PoC")
    print(f" Collector  : tiap {max(60, COLLECT_INTERVAL)}s | Breaking: "
          + ("off" if args.no_rss else f"tiap {max(30, BREAKING_INTERVAL)}s"))
    print(" Stop       : Ctrl+C")
    print("=" * 62)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
