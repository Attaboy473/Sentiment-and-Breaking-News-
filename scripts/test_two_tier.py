"""Validasi two-tier resolve_direction di 56 post uji (hasil di stdout, baris TEST dibuang)."""
import csv
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo")
os.environ["COHERE_API_KEY"] = os.environ.get("COHERE_API_KEY", "")
from ml.search.chatter_direction import resolve_direction  # noqa: E402

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))
final = {r["postid"]: (r["label2"] or r["label"]) for r in rows}
con = sqlite3.connect(BASE + r"\demo.db")
texts = {r[0]: r[1] for r in con.execute("SELECT postid, text_original FROM stream_posts")}
con.close()

stat = {"high": [0, 0], "medium": [0, 0]}
n_flip = 0
for r in rows:
    lab = final[r["postid"]]
    if lab == "neutral":
        continue  # hanya post berarah yang diuji
    pid = int(r["postid"])
    label, score, mv = resolve_direction(pid, "TEST", texts.get(pid, ""), "neutral", 0.0)
    if mv == "semantic-knn-v1":
        n_flip += 1
        con = sqlite3.connect(BASE + r"\demo.db")
        tier = con.execute(
            "SELECT tier FROM semantic_direction_log WHERE postid=? AND ticker='TEST'",
            (pid,)).fetchone()
        con.close()
        tier = (tier[0] if tier and tier[0] else "high")
        stat.setdefault(tier, [0, 0])
        stat[tier][0] += 1
        stat[tier][1] += (1 if label == lab else 0)

print(f"TOTAL FLIP: {n_flip}")
for t, (n, ok) in stat.items():
    if n:
        print(f"  tier {t:7s}: {ok}/{n} = {ok / n:.0%}")

# bersihkan baris uji dari audit log
con = sqlite3.connect(BASE + r"\demo.db", timeout=15)
con.execute("DELETE FROM semantic_direction_log WHERE ticker='TEST'")
con.commit()
con.close()
print("log TEST dibersihkan")
