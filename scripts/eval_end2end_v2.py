"""Eval end-to-end v2: pairing ticker dari CSV (bukan top-confidence).

Baseline BERSIH pipeline (lexicon-v3 + resolver two-tier) di 150 pilot.
"""
import csv
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo")
os.environ["COHERE_API_KEY"] = os.environ.get("COHERE_API_KEY", "")
from ml.search.chatter_direction import resolve_direction  # noqa: E402

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))

con = sqlite3.connect(BASE + r"\demo.db", timeout=15)
texts = {r[0]: r[1] for r in con.execute("SELECT postid, text_original FROM stream_posts")}
lex = {}
for pid, tk, sent in con.execute(
        "SELECT postid, ticker, sentiment FROM sentiment_per_ticker WHERE model_version='lexicon-v3'"):
    lex[(pid, tk)] = sent
# ticker yg tersedia di DB per post (fallback kalau CSV ticker gak ada skor lexiconnya)
db_tk = {}
for pid, tk in con.execute("SELECT postid, ticker FROM stream_post_tickers"):
    db_tk.setdefault(pid, []).append(tk)
con.close()

stat = {"dir_ok": 0, "dir_bad": 0, "neu_ok": 0, "neu_ff": 0}
errors = []
n = 0
for r in rows:
    gold = (r.get("label2") or r.get("label") or "").strip().lower()
    if gold not in ("bullish", "bearish", "neutral"):
        continue
    pid = int(r["postid"])
    tk = (r.get("ticker") or "").strip().upper()
    text = texts.get(pid, "")
    if not text:
        continue
    n += 1
    lex_sent = lex.get((pid, tk), "neutral")
    if lex_sent in ("bullish", "bearish"):
        pred, via = lex_sent, "lexicon"
    else:
        label, score, mv = resolve_direction(pid, tk, text, "neutral", 0.0)
        pred = label if mv == "semantic-knn-v1" else "neutral"
        via = "resolver" if pred != "neutral" else "abstain"
    if gold == "neutral":
        if pred == "neutral":
            stat["neu_ok"] += 1
        else:
            stat["neu_ff"] += 1
            errors.append((f"false-flip({via})", gold, pred, tk, text[:60]))
    elif pred == gold:
        stat["dir_ok"] += 1
    else:
        stat["dir_bad"] += 1
        errors.append((f"arah-salah({via})", gold, pred, tk, text[:60]))

dir_tot = stat["dir_ok"] + stat["dir_bad"]
neu_tot = stat["neu_ok"] + stat["neu_ff"]
print(f"EVAL v2 (ticker CSV) — {n} post:")
print(f"  gold berarah: benar {stat['dir_ok']}/{dir_tot} = {stat['dir_ok'] / dir_tot:.0%}")
print(f"  gold neutral: dibiarkan {stat['neu_ok']}/{neu_tot} = {stat['neu_ok'] / neu_tot:.0%}")
acc = (stat["dir_ok"] + stat["neu_ok"]) / n
print(f"  AKURASI TOTAL: {stat['dir_ok'] + stat['neu_ok']}/{n} = {acc:.0%}")
print("\nsemua error:")
for kind, g, p, t, txt in errors:
    print(f"  [{kind}] gold={g} pred={p} ({t}) :: {txt}")
