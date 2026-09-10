"""Eval end-to-end v3: recompute LIVE dgn kode sentiment saat ini (lexicon + resolver).

Mirror persis alur ingest app.py:
  sentiment_for_ticker(text, tk)  -> None ? sentiment_score(text) : pakai itu
  label neutral -> resolve_direction (two-tier Cohere)
Gold = label2 (pass-2) kalau ada, selain itu label. Ticker pairing dari CSV.
"""
import csv
import os
import sqlite3
import sys

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
sys.path.insert(0, BASE)
os.environ["COHERE_API_KEY"] = os.environ.get("COHERE_API_KEY", "")

from app import sentiment_for_ticker, sentiment_score  # noqa: E402
from ml.search.chatter_direction import resolve_direction  # noqa: E402

con = sqlite3.connect(BASE + r"\demo.db", timeout=15)
texts = {r[0]: r[1] for r in con.execute("SELECT postid, text_original FROM stream_posts")}
con.close()

rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))
stat = {"dir_ok": 0, "dir_bad": 0, "neu_ok": 0, "neu_ff": 0}
via_stat = {"lexicon": [0, 0], "resolver": [0, 0], "abstain": [0, 0]}
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
    v2 = sentiment_for_ticker(text, tk)
    if v2 is None:
        v2 = sentiment_score(text)  # fallback whole-post, sama kayak ingest
    label, score = v2[0], v2[1]
    if label in ("bullish", "bearish"):
        pred, via = label, "lexicon"
    else:
        lab2, _sc, mv = resolve_direction(pid, "EVAL", text, "neutral", 0.0)
        pred = lab2 if mv == "semantic-knn-v1" else "neutral"
        via = "resolver" if pred != "neutral" else "abstain"
    key = "ok" if (gold == "neutral" and pred == "neutral") or gold == pred else "bad"
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
    via_stat.setdefault(via, [0, 0])
    via_stat[via][0] += 1
    if key == "ok":
        via_stat[via][1] += 1

dir_tot = stat["dir_ok"] + stat["dir_bad"]
neu_tot = stat["neu_ok"] + stat["neu_ff"]
print(f"EVAL v3 (recompute live, kode saat ini) — {n} post:")
print(f"  gold berarah: benar {stat['dir_ok']}/{dir_tot} = {stat['dir_ok'] / dir_tot:.0%}")
print(f"  gold neutral: dibiarkan {stat['neu_ok']}/{neu_tot} = {stat['neu_ok'] / neu_tot:.0%}")
acc = (stat["dir_ok"] + stat["neu_ok"]) / n
print(f"  AKURASI TOTAL: {stat['dir_ok'] + stat['neu_ok']}/{n} = {acc:.0%}")
print("\nper-jalur keputusan (benar/total):")
for via, (tot, ok) in via_stat.items():
    if tot:
        print(f"  {via:9s}: {ok}/{tot} = {ok / tot:.0%}")
print(f"\nerror tersisa ({len(errors)}):")
for kind, g, p, t, txt in errors:
    print(f"  [{kind}] gold={g} pred={p} ({t}) :: {txt}")

# bersihkan jejak eval di audit log
con = sqlite3.connect(BASE + r"\demo.db", timeout=15)
con.execute("DELETE FROM semantic_direction_log WHERE ticker='EVAL'")
con.commit()
con.close()
