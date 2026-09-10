"""Kalibrasi threshold tier medium: presisi per bucket sim di 56 post uji."""
import csv
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo")
os.environ["COHERE_API_KEY"] = os.environ.get("COHERE_API_KEY", "")
from ml.search.chatter_direction import semantic_direction_ex  # noqa: E402

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))
final = {r["postid"]: (r["label2"] or r["label"]) for r in rows}
con = sqlite3.connect(BASE + r"\demo.db")
texts = {r[0]: r[1] for r in con.execute("SELECT postid, text_original FROM stream_posts")}
con.close()

results = []  # (tier, sim, benar)
for r in rows:
    lab = final[r["postid"]]
    if lab == "neutral":
        continue
    pid = int(r["postid"])
    hint = semantic_direction_ex(texts.get(pid, ""), exclude_pid=pid)
    if not hint:
        continue
    tier = hint.get("tier", "high")
    if tier != "medium":
        continue
    results.append((hint["sim"], hint["direction"] == lab))

print("bucket sim (medium tier):")
for lo in (0.60, 0.65, 0.70, 0.75, 0.80):
    sub = [(s, ok) for s, ok in results if s >= lo]
    hi = [(s, ok) for s, ok in results if lo <= s < lo + 0.05]
    if hi:
        n, k = len(hi), sum(ok for _, ok in hi)
        print(f"  [{lo:.2f}-{lo+0.05:.2f}): {k}/{n} = {k / n:.0%}" if n else "", end="")
    if sub:
        n, k = len(sub), sum(ok for _, ok in sub)
        print(f"   |  >= {lo:.2f}: {k}/{n} = {k / n:.0%}")
    else:
        print()

print("selesai")
