"""Simulasi arbitration: kNN sebagai veto/endorsement atas keputusan lexicon.

Untuk tiap post yg lexicon-v3 bilang BERARAH (45 post di eval):
  - hitung kNN top-3 (LOO) seperti resolver
  - klasifikasi hubungan: CONTRADICT (sepakat arah lawan) / AGREE / abstain
  - ukur: catch-rate (berapa % error lexicon ketangkep) vs false-arrest
    (berapa % lexicon yg BENAR malah diveto)
"""
import csv
import math
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo")
os.environ.setdefault("COHERE_API_KEY", "")
from ml.search.chatter_direction import _pool, _query_vec, MODEL_COHERE, DB_PATH  # noqa: E402

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))

con = sqlite3.connect(DB_PATH, timeout=15)
texts = {r[0]: r[1] for r in con.execute("SELECT postid, text_original FROM stream_posts")}
lex = {}
for pid, tk, sent in con.execute(
        "SELECT postid, ticker, sentiment FROM sentiment_per_ticker WHERE model_version='lexicon-v3'"):
    lex[(pid, tk)] = sent
con.close()

pool = _pool(MODEL_COHERE)
pool_norm = [(pid, lab, v, math.sqrt(sum(x * x for x in v)) or 1.0) for pid, lab, v in pool]

matrix = {("err", "CONTRADICT"): 0, ("err", "AGREE"): 0, ("err", "abstain"): 0,
          ("ok", "CONTRADICT"): 0, ("ok", "AGREE"): 0, ("ok", "abstain"): 0}
opp = {"bullish": "bearish", "bearish": "bullish"}
examples = {"err-CONTRADICT": [], "ok-CONTRADICT": []}

for r in rows:
    gold = (r.get("label2") or r.get("label") or "").strip().lower()
    if gold not in ("bullish", "bearish", "neutral"):
        continue
    pid = int(r["postid"])
    tk = (r.get("ticker") or "").strip().upper()
    lex_sent = lex.get((pid, tk), lex.get((pid, "?"), "neutral"))
    if lex_sent not in ("bullish", "bearish"):
        continue
    text = texts.get(pid, "")
    qv = _query_vec(text, MODEL_COHERE)
    qn = math.sqrt(sum(x * x for x in qv)) or 1.0
    sims = sorted(
        ((sum(a / qn * b / on for a, b in zip(qv, ov)), olab)
         for opid, olab, ov, on in pool_norm if opid != pid),
        reverse=True)
    top3 = [(s, l) for s, l in sims[:3] if s <= 0.99]
    labels = {l for _, l in top3}
    if len(top3) == 3 and len(labels) == 1 and top3[2][0] >= 0.60:
        rel = "AGREE" if top3[0][1] == lex_sent else "CONTRADICT"
    else:
        rel = "abstain"
    key = "err" if lex_sent != gold else "ok"
    matrix[(key, rel)] += 1
    if key == "err" and rel == "CONTRADICT" and len(examples["err-CONTRADICT"]) < 4:
        examples["err-CONTRADICT"].append((gold, lex_sent, text[:70]))
    if key == "ok" and rel == "CONTRADICT" and len(examples["ok-CONTRADICT"]) < 4:
        examples["ok-CONTRADICT"].append((gold, lex_sent, text[:70]))

n_err = sum(v for (k, _), v in matrix.items() if k == "err")
n_ok = sum(v for (k, _), v in matrix.items() if k == "ok")
print(f"lexicon berarah: {n_err + n_ok} (gold: benar {n_ok}, salah {n_err})")
print("\nrelasi kNN (high tier saja):")
for k, n_ok2, n_err2 in (("AGREE", matrix[("ok", "AGREE")], matrix[("err", "AGREE")]),
                         ("CONTRADICT", matrix[("ok", "CONTRADICT")], matrix[("err", "CONTRADICT")]),
                         ("abstain", matrix[("ok", "abstain")], matrix[("err", "abstain")])):
    tot = n_ok2 + n_err2
    print(f"  {k:10s}: {tot:2d} kasus (lexicon benar {n_ok2}, salah {n_err2})")
catch = matrix[("err", "CONTRADICT")]
false_arrest = matrix[("ok", "CONTRADICT")]
print(f"\nveto CONTRADICT->neutral: nangkep {catch}/{n_err} error "
      f"({catch / n_err:.0%} catch-rate), korban {false_arrest}/{n_ok} yg benar "
      f"({false_arrest / n_ok:.0%} false-arrest)")
print("\ncontoh error yg ketangkep:")
for g, p, t in examples["err-CONTRADICT"]:
    print(f"  gold={g} lex={p} :: {t}")
print("contoh yg benar tapi kena veto:")
for g, p, t in examples["ok-CONTRADICT"]:
    print(f"  gold={g} lex={p} :: {t}")
