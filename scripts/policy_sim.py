"""Bedah loss akurasi end-to-end + simulasi kebijakan gate alternatif.

Untuk tiap post uji: hitung top-3 tetangga pool (LOO) sendiri (tanpa gate),
lalu evaluasi kebijakan:
  CUR    = sekarang (high k=3>=0.60 sepakat; medium k=1>=0.70)
  P1     = k=2 sepakat, kedua >= 0.65
  P2     = k=2 sepakat, kedua >= 0.68
  P3     = k=2 sepakat, kedua >= 0.65 DAN (top1-top2) <= 0.08
  P4     = k=1 >= 0.65
Output: per kebijakan -> recall berarah, presisi flip, false-flip di gold neutral.
"""
import csv
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo")
os.environ.setdefault("COHERE_API_KEY", "")
from ml.search.chatter_direction import _pool, _query_vec, MODEL_COHERE  # noqa: E402
from ml.search.chatter_direction import DB_PATH  # noqa: E402
import math  # noqa: E402

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))

con = sqlite3.connect(DB_PATH, timeout=15)
texts = {r[0]: r[1] for r in con.execute("SELECT postid, text_original FROM stream_posts")}
lex = {}
for pid, tk, sent in con.execute(
        "SELECT postid, ticker, sentiment FROM sentiment_per_ticker WHERE model_version='lexicon-v3'"):
    lex.setdefault(pid, sent)
con.close()

pool = _pool(MODEL_COHERE)
pool_norm = [(pid, lab, v, math.sqrt(sum(x * x for x in v)) or 1.0) for pid, lab, v in pool]

recs = []
for r in rows:
    gold = (r.get("label2") or r.get("label") or "").strip().lower()
    if gold not in ("bullish", "bearish", "neutral"):
        continue
    pid = int(r["postid"])
    text = texts.get(pid, "")
    if not text:
        continue
    if lex.get(pid) in ("bullish", "bearish"):
        recs.append((gold, "lexicon", lex[pid], text, None))
        continue
    qv = _query_vec(text, MODEL_COHERE)
    qn = math.sqrt(sum(x * x for x in qv)) or 1.0
    sims = []
    for opid, olab, ov, on in pool_norm:
        if opid == pid:
            continue  # LOO
        sims.append((sum(a / qn * b / on for a, b in zip(qv, ov)), olab))
    sims.sort(reverse=True)
    top3 = [(round(s, 3), l) for s, l in sims[:3] if s <= 0.99]
    recs.append((gold, "knn", None, text, top3))

print(f"dekomposisi {len(recs)} post:")
lex_dir = [x for x in recs if x[1] == "lexicon"]
lex_ok = sum(1 for g, _, p, _, _ in lex_dir if g == p)
lex_flip_neu = sum(1 for g, _, _, _, _ in lex_dir if g == "neutral")
print(f"  lexicon berkata arah : {len(lex_dir)} (benar {lex_ok}, salah-flip neutral {lex_flip_neu})")
knn = [x for x in recs if x[1] == "knn"]
dir_gold = [x for x in knn if x[0] != "neutral"]
neu_gold = [x for x in knn if x[0] == "neutral"]
print(f"  sisa ke resolver     : {len(knn)} (gold berarah {len(dir_gold)}, gold neutral {len(neu_gold)})")


def eval_policy(name, fn):
    flip_ok = flip_bad = miss = ff = 0
    for g, _, _, _, top3 in dir_gold:
        d = fn(top3)
        if d is None:
            miss += 1
        elif d == g:
            flip_ok += 1
        else:
            flip_bad += 1
    for g, _, _, _, top3 in neu_gold:
        if fn(top3) is not None:
            ff += 1
    tot = flip_ok + flip_bad
    prec = f"{flip_ok / tot:.0%}" if tot else "-"
    print(f"  {name:6s}: flip {tot:3d} | presisi {prec} ({flip_ok}/{tot}) | lepas {miss} | false-flip neutral {ff}")


def cur(top3):
    if not top3:
        return None
    labs = {l for _, l in top3[:3]}
    if len(top3) == 3 and len(labs) == 1 and top3[0][0] >= 0.60 and top3[2][0] >= 0.60:
        return top3[0][1]
    if top3[0][0] >= 0.70:
        return top3[0][1]
    return None


def p1(top3):
    if len(top3) >= 2 and top3[0][1] == top3[1][1] and top3[1][0] >= 0.65:
        return top3[0][1]
    return None


def p2(top3):
    if len(top3) >= 2 and top3[0][1] == top3[1][1] and top3[1][0] >= 0.68:
        return top3[0][1]
    return None


def p3(top3):
    if (len(top3) >= 2 and top3[0][1] == top3[1][1] and top3[1][0] >= 0.65
            and top3[0][0] - top3[1][0] <= 0.08):
        return top3[0][1]
    return None


def p4(top3):
    if top3 and top3[0][0] >= 0.65:
        return top3[0][1]
    return None


print("\nsimulasi kebijakan (di bagian resolver saja):")
eval_policy("CUR", cur)
eval_policy("P1", p1)
eval_policy("P2", p2)
eval_policy("P3", p3)
eval_policy("P4", p4)
