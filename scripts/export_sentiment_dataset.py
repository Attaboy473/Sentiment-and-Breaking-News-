"""Export pilot dataset sentiment dari demo.db -> CSV annotasi (dok IndoBERT 8.2/13.1).

Pre-fill label_pred dari lexicon-v3 biar annotator cukup KOREKSI, bukan nulis dari nol
(active learning, dok 37). Sampel sengaja dicampur: yang ada lexicon hit DAN yang polos,
biar gak cuma nge-label kasus obvious (anti-pattern dok 40).
Group by postid -> split train/test per-group (anti leakage, dok 8.9).

Pakai:  python scripts/export_sentiment_dataset.py  [jumlah_baris=150]
Output: data/sentiment/annotation_pilot.csv
"""
import csv
import os
import random
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import TICKERS, sentiment_for_ticker, sentiment_score  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "sentiment", "annotation_pilot.csv")


def target_context(text: str, ticker: str) -> str:
    """Segmen milik ticker (mirror logic per-cashtag v2): teks sebelum cashtag
    pertama ikut cashtag pertama; berakhir sebelum cashtag berikutnya."""
    t = re.sub(r"https?://\S+", " ", (text or "").lower())
    toks = re.findall(r"[a-z0-9]+|\$[a-z]{4}", t)
    sym = "$" + ticker.lower()
    if sym not in toks:
        return (text or "").strip()
    i = toks.index(sym)
    bs = [k for k, w in enumerate(toks) if w.startswith("$")]
    idx = bs.index(i)
    start = 0 if idx == 0 else i + 1
    end = bs[idx + 1] if idx + 1 < len(bs) else len(toks)
    return " ".join(toks[start:i] + toks[i + 1:end]).strip()


def main() -> None:
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    rng = random.Random(42)
    c = sqlite3.connect(os.path.join(BASE, "demo.db"))
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT spt.postid, sp.text_original, spt.ticker "
        "FROM stream_post_tickers spt JOIN stream_posts sp ON sp.postid = spt.postid "
        "WHERE spt.ticker IN (%s) AND length(sp.text_original) > 8" % ",".join("?" * len(TICKERS)),
        TICKERS,
    ).fetchall()

    seen_text, cand = set(), []
    for r in rows:
        text = (r["text_original"] or "").strip()
        key = re.sub(r"\s+", " ", text.lower())[:80]
        if key in seen_text:
            continue
        seen_text.add(key)
        label, score, hits = sentiment_score(text)
        seg = target_context(text, r["ticker"])
        v2 = sentiment_for_ticker(text, r["ticker"]) or (label, score)
        neg_hits = [h for h in hits if h.startswith("!")]
        cand.append({
            "postid": r["postid"], "ticker": r["ticker"], "text": text,
            "target_context": seg,
            "label_pred": v2[0], "score_pred": v2[1],
            "bucket": ("multi" if False else
                       "negation" if neg_hits else
                       "no_hit" if not hits else "bull_bear"),
        })

    # postid dengan >1 ticker = kasus multi-ticker (dok 8.3)
    by_post = {}
    for x in cand:
        by_post.setdefault(x["postid"], set()).add(x["ticker"])
    for x in cand:
        if len(by_post[x["postid"]]) > 1:
            x["bucket"] = "multi"

    quotas = {"multi": 30, "bull_bear": 60, "negation": 20, "no_hit": 40}
    picked = []
    for b, q in quotas.items():
        pool = [x for x in cand if x["bucket"] == b]
        rng.shuffle(pool)
        picked.extend(pool[:q])
    rest = [x for x in cand if x not in picked]
    rng.shuffle(rest)
    picked.extend(rest[:max(0, n_target - len(picked))])
    picked.sort(key=lambda x: x["postid"])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "postid", "ticker", "text", "target_context",
                    "label", "label_pred", "score_pred", "bucket", "notes"])
        for i, x in enumerate(picked, 1):
            w.writerow([i, x["postid"], x["ticker"], x["text"], x["target_context"],
                        "", x["label_pred"], x["score_pred"], x["bucket"], ""])
    from collections import Counter
    print("export", len(picked), "baris ->", os.path.relpath(OUT, BASE))
    print("bucket:", dict(Counter(x["bucket"] for x in picked)))
    print("label_pred:", dict(Counter(x["label_pred"] for x in picked)))


if __name__ == "__main__":
    main()
