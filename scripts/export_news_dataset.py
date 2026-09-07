"""Export dataset materiality news -> CSV annotasi (dok IndoBERT 9.1/13.2).

Sumber: news_articles di demo.db + tarikan RSS segar sekali jalan.
label_pred = proxy rule (keyword material + denial guard); hard negatives
(baris ber-denial) WAJIB ikut (dok 9.2) - di-flag biar gak ketumpukan.

Pakai:  python scripts/export_news_dataset.py  [jumlah_baris=120]
Output: data/news/annotation_pilot.csv
"""
import csv
import hashlib
import os
import sqlite3
import sys
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (ENTITY_CONF_HIGH, IMPACT_KEYWORDS, RSS_SOURCES,  # noqa: E402
                 child_link, child_text, clean_text, denial_hits, fetch_url,
                 lname, norm, resolve_tickers)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "news", "annotation_pilot.csv")


def collect_rows() -> list[dict]:
    out = {}
    c = sqlite3.connect(os.path.join(BASE, "demo.db"))
    for uh, src, link, title, summary, pub, score, ticks in c.execute(
        "SELECT url_hash, source_name, source_url, title, summary, published_at,"
        " rule_score, tickers FROM news_articles"
    ):
        out[uh] = {"source": src, "url": link, "title": title, "summary": summary or "",
                   "published": pub, "rule_score": score, "tickers_db": ticks}
    for src in RSS_SOURCES:  # tarikan segar sekali (bypass window DB)
        try:
            root = ET.fromstring(fetch_url(src["url"]))
        except Exception:
            continue
        for node in root.iter():
            if lname(node.tag) not in {"item", "entry"}:
                continue
            title = clean_text(child_text(node, {"title"}), 300)
            if not title:
                continue
            summary = clean_text(child_text(node, {"description", "summary", "content", "encoded"}), 1200)
            link = child_link(node)
            uh = hashlib.sha1((link or title).encode()).hexdigest()
            out.setdefault(uh, {"source": src["name"], "url": link, "title": title,
                                "summary": summary, "published": "", "rule_score": None,
                                "tickers_db": "[]"})
    return list(out.values())


def main() -> None:
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    rows = collect_rows()
    anno = []
    for r in rows:
        ntext = norm(r["title"] + " " + r["summary"])
        kw = {k: v for k, v in IMPACT_KEYWORDS.items() if norm(k) in ntext}
        denial = denial_hits(ntext)
        ticks = sorted({t for t, cf, _ in resolve_tickers(r["title"] + " " + r["summary"]) if cf >= 0.7})
        if denial:
            pred, bucket = "non_material", "hard_negative"
        elif kw:
            pred, bucket = "material", "keyword_hit"
        else:
            pred, bucket = "non_material", "plain"
        anno.append({
            "headline": r["title"], "summary": r["summary"][:300],
            "tickers": ",".join(ticks), "label": "", "label_pred": pred,
            "denial_hits": ";".join(denial), "matched_keywords": ";".join(kw),
            "source": r["source"], "published": r["published"], "bucket": bucket,
        })
    hard = [x for x in anno if x["bucket"] == "hard_negative"]
    rest = [x for x in anno if x["bucket"] != "hard_negative"]
    picked = hard + rest[:max(0, n_target - len(hard))]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "headline", "summary", "tickers", "label",
                    "label_pred", "denial_hits", "matched_keywords", "source", "published", "bucket"])
        for i, x in enumerate(picked, 1):
            w.writerow([i, x["headline"], x["summary"], x["tickers"], x["label"],
                        x["label_pred"], x["denial_hits"], x["matched_keywords"],
                        x["source"], x["published"], x["bucket"]])
    from collections import Counter
    print("export", len(picked), "baris ->", os.path.relpath(OUT, BASE))
    print("bucket:", dict(Counter(x["bucket"] for x in picked)))
    print("label_pred:", dict(Counter(x["label_pred"] for x in picked)))


if __name__ == "__main__":
    main()
