"""End-to-end akurasi bull/bear pipeline SAAT INI di 150 label pilot.

Simulasi pipeline produksi per post:
  lexicon-v3 (DB) -> kalau directional = prediksi
                  -> kalau neutral    = resolve_direction (two-tier Cohere)
Prediksi vs gold final (label2 kalau ada, selain itu label).
"""
import csv
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo")
os.environ.setdefault("COHERE_API_KEY", "")
from ml.search.chatter_direction import resolve_direction  # noqa: E402

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))
print("kolom CSV:", list(rows[0].keys()))

con = sqlite3.connect(BASE + r"\demo.db", timeout=15)
texts = {r[0]: r[1] for r in con.execute("SELECT postid, text_original FROM stream_posts")}
tickers = {}
for pid, tk, conf in con.execute(
        "SELECT postid, ticker, COALESCE(confidence,0) FROM stream_post_tickers"):
    if pid not in tickers or conf > tickers[pid][1]:
        tickers[pid] = (tk, conf)
lex = {}
for pid, tk, sent in con.execute(
        "SELECT postid, ticker, sentiment FROM sentiment_per_ticker WHERE model_version='lexicon-v3'"):
    lex[(pid, tk)] = sent
con.close()

stat = {"dir_correct": 0, "dir_wrong": 0, "neu_ok": 0, "neu_wrongflip": 0}
errors = []
n_eval = 0
for r in rows:
    gold = (r.get("label2") or r.get("label") or "").strip().lower()
    if gold not in ("bullish", "bearish", "neutral"):
        continue
    pid = int(r["postid"])
    tk = tickers.get(pid, ("?", 0))[0]
    text = texts.get(pid, "")
    if not text:
        continue
    n_eval += 1
    lex_sent = lex.get((pid, tk), "neutral")
    if lex_sent in ("bullish", "bearish"):
        pred = lex_sent
        if gold == "neutral":
            stat["neu_wrongflip"] += 1
            errors.append(("lexicon salah flip", gold, pred, text[:60]))
        elif pred == gold:
            stat["dir_correct"] += 1
        else:
            stat["dir_wrong"] += 1
            errors.append(("lexicon arah salah", gold, pred, text[:60]))
    else:
        label, score, mv = resolve_direction(pid, tk, text, "neutral", 0.0)
        pred = label if mv == "semantic-knn-v1" else "neutral"
        if gold == "neutral":
            if pred == "neutral":
                stat["neu_ok"] += 1
            else:
                stat["neu_wrongflip"] += 1
                errors.append(("resolver salah flip", gold, pred, text[:60]))
        elif pred == gold:
            stat["dir_correct"] += 1
        else:
            stat["dir_wrong"] += 1
            errors.append(("resolver arah salah/abstain", gold, pred, text[:60]))

dir_total = stat["dir_correct"] + stat["dir_wrong"]
neu_total = stat["neu_ok"] + stat["neu_wrongflip"]
print(f"\nEVAL {n_eval} post (dengan pipeline penuh sekarang):")
print(f"  GOLD berarah   : {stat['dir_correct']}/{dir_total} = {stat['dir_correct']/dir_total:.0%} benar arah")
print(f"  GOLD neutral   : {stat['neu_ok']}/{neu_total} = {stat['neu_ok']/neu_total:.0%} dibiarkan neutral (abstain bener)")
overall = (stat["dir_correct"] + stat["neu_ok"]) / n_eval
print(f"  AKURASI TOTAL  : {stat['dir_correct'] + stat['neu_ok']}/{n_eval} = {overall:.0%}")
print("\nContoh error (maks 8):")
for kind, g, p, t in errors[:8]:
    print(f"  [{kind}] gold={g} pred={p} :: {t}")
