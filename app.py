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
import json
import os
import re
import sqlite3
import sys
import threading
import time
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
CANDIDATE_THRESHOLD = 45.0
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def wib_str(dt: datetime) -> str:
    return dt.astimezone(WIB).strftime("%d %b %H:%M")


def author_hash(username: str) -> str:
    return "sb:" + hashlib.sha1(f"mf-demo:{username}".encode()).hexdigest()[:10]


# --------------------------------------------------------------------------
# Sentiment (demo-grade lexicon heuristic)
# --------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^a-z0-9$\s]", " ", t)
    return [w for w in SPACE_RE.sub(" ", t).split() if w]


def sentiment_score(text: str) -> tuple[str, float, list[str]]:
    """Returns (label, score -1..1, matched_hits). Negation flips a hit."""
    toks = [_norm_token(w) for w in tokenize(text)]
    hits: list[str] = []
    bull = bear = 0
    for i, w in enumerate(toks):
        val = 0
        if w in LEX_BULL:
            val = 1
        elif w in LEX_BEAR:
            val = -1
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
        for i, w in enumerate(seg):
            val = 0
            if w in LEX_BULL:
                val = 1
            elif w in LEX_BEAR:
                val = -1
            if not val:
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
                        json.dumps(p, ensure_ascii=False, default=str)[:8000],
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
                    # v2: skor per-cashtag (post multi-ticker gak lagi "sama rata")
                    joined = dict.fromkeys([sym] + [x for x in (p.get("topics") or []) if isinstance(x, str)])
                    for t in joined:
                        if not re.fullmatch(r"[A-Z]{4}", str(t)):
                            continue
                        v2 = sentiment_for_ticker(p.get("content_original") or "", str(t))
                        if v2 is None:
                            v2 = (label, score)  # fallback whole-post
                        c.execute(
                            "INSERT OR REPLACE INTO sentiment_per_ticker "
                            "(postid, ticker, model_version, sentiment, score, processed_at) "
                            "VALUES (?,?,?,?,?,?)",
                            (pid, t, "lexicon-v2", v2[0], v2[1], iso(now_utc())),
                        )
                for t in dict.fromkeys([sym] + [x for x in (p.get("topics") or []) if isinstance(x, str)]):
                    if re.fullmatch(r"[A-Z]{4}", str(t)):
                        c.execute(
                            "INSERT OR IGNORE INTO stream_post_tickers (postid, ticker, relation_source, confidence) "
                            "VALUES (?,?,?,?)",
                            (pid, t, "server_topic", 0.9),
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
                       CASE WHEN spv.postid IS NOT NULL THEN 'lexicon-v2' ELSE 'lexicon-demo-v1' END AS model_used
                FROM stream_post_tickers spt
                JOIN stream_posts sp ON sp.postid = spt.postid
                LEFT JOIN sentiment_per_ticker spv
                       ON spv.postid = sp.postid AND spv.ticker = spt.ticker
                      AND spv.model_version = 'lexicon-v2'
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
    stats = {"sources_ok": 0, "candidates": 0, "triggered": 0, "errors": []}
    ticker_map = {t: [t.lower()] for t in TICKERS}
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
            title = child_text(node, {"title"})
            if not title:
                continue
            summary = child_text(node, {"description", "summary", "content", "encoded"})
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
            tickers = detect_entities(full_text, ticker_map)
            sectors = detect_entities(full_text, SECTOR_ALIASES)
            keywords = {k: v for k, v in IMPACT_KEYWORDS.items() if norm(k) in norm(full_text)}
            score, _reasons = rule_score(published, float(src["priority"]), tickers, sectors, keywords)
            score = min(score, 100.0)

            # --- simpan semua artikel (window 48 jam) biar view News ramai ---
            url_hash = hashlib.sha1((link or title).encode()).hexdigest()
            with conn() as c:
                c.execute(
                    "INSERT OR IGNORE INTO news_articles "
                    "(url_hash, source_name, source_url, title, summary, published_at, fetched_at,"
                    " rule_score, tickers, sectors, keywords, category) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        url_hash, src["name"], link, title, (summary or "")[:400],
                        iso(published), iso(now_utc()), score,
                        json.dumps(tickers), json.dumps(sectors), json.dumps(keywords),
                        category,
                    ),
                )

            # --- jalur breaking event (hanya item segar + lolos threshold) ---
            age_min = (now_utc() - published).total_seconds() / 60
            if age_min > MAX_ITEM_AGE_MIN:
                continue
            if score < CANDIDATE_THRESHOLD:
                continue

            ehash = hashlib.sha256(f"{norm(title)}|{link}".encode()).hexdigest()
            # Rule-only validator proxy (demo): confidence scales with score.
            confidence = round(min(0.9, 0.5 + score * 0.004), 2)
            severity = derive_severity(score, confidence)
            status = "CANDIDATE"
            if tickers and severity in ("HIGH", "CRITICAL"):
                status = "TRIGGERED"
                stats["triggered"] += 1
            stats["candidates"] += 1

            with conn() as c:
                c.execute(
                    "INSERT OR IGNORE INTO breaking_events "
                    "(event_hash, source_name, source_url, headline, published_at, detected_at,"
                    " rule_score, reasons, matched_keywords, tickers, sectors, confidence, severity, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ehash, src["name"], link, title, iso(published), iso(now_utc()),
                        score, json.dumps(reasons), json.dumps(keywords),
                        json.dumps(tickers), json.dumps(sectors), confidence, severity, status,
                    ),
                )
                if status == "TRIGGERED":
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
            c.execute(
                "UPDATE recommendations SET score=?, grade=?, stale=0,"
                " reassessed_at=?, reassessed_note=? WHERE ticker=?",
                (
                    new_score,
                    grade_for(new_score),
                    iso(now_utc()),
                    f"simulated targeted reassessment (delta {delta:+d})",
                    t,
                ),
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
            " rule_score, tickers, category FROM news_articles "
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
            "sentiment_model": "lexicon-v2 per-cashtag (HEURISTIC, belum dikalibrasi)",
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
            elif path == "/api/chatter":
                with conn() as c:
                    rows = [dict(r) for r in c.execute(
                        "SELECT sp.postid, sp.text_original AS text, sp.created_at_utc AS created_at, "
                        "sp.likes, sp.replies, sp.flags, "
                        "COALESCE(spv.sentiment, sen.sentiment) AS sentiment "
                        "FROM stream_posts sp "
                        "JOIN stream_post_tickers spt ON spt.postid = sp.postid "
                        "LEFT JOIN sentiment_per_ticker spv "
                        "  ON spv.postid = sp.postid AND spv.ticker = spt.ticker "
                        " AND spv.model_version = 'lexicon-v2' "
                        "LEFT JOIN sentiment_predictions sen "
                        "  ON sen.postid = sp.postid AND sen.model_version = 'lexicon-demo-v1' "
                        "WHERE spt.ticker = ? AND sp.created_at_utc IS NOT NULL "
                        "ORDER BY sp.created_at_utc DESC LIMIT 25",
                        (self.path.rsplit("ticker=", 1)[-1].split("&")[0].upper(),),
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
                with conn() as c:
                    c.executescript(
                        "DELETE FROM stream_post_tickers; DELETE FROM sentiment_predictions;"
                        " DELETE FROM stream_posts; DELETE FROM breaking_events;"
                        " UPDATE recommendations SET stale=0, stale_reason=NULL, stale_event_hash=NULL,"
                        " stale_at=NULL, reassessed_at=NULL, reassessed_note=NULL;"
                    )
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
    s, r = rule_score(now_utc() - timedelta(minutes=2), 1.0, ["ANTM"], ["Basic Materials"], {"produksi dihentikan": 24})
    check("rule score material > plain", s >= 45 and any("hard-event" in x for x in r))
    s_plain, _ = rule_score(now_utc() - timedelta(minutes=2), 1.0, ["ANTM"], [], {})
    check("material > plain", s > s_plain)
    check("severity derive", derive_severity(60, 0.74) == "HIGH")
    check("author hash stable", author_hash("a") == author_hash("a") and author_hash("a") != author_hash("b"))
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
    threading.Thread(target=collector_loop, daemon=True).start()
    threading.Thread(target=lambda: (time.sleep(1), collect_once()), daemon=True).start()

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
