"""Benchmark + kalibrasi Cohere embed-v4.0 di data pilot kita (150 post, 40 berita).

Output:
1. Direction resolution (neutral -> arah): presisi di beberapa threshold,
   dibandingin MiniLM (60% di sim>=0.75 k=3).
2. kNN full-label (LOO): pembanding lexicon 56.7% / MiniLM 46.7%.
3. News materiality margin: threshold optimal, dibandingin lexicon 37/40.
4. Spot check search parafrase.
Token VIA ENV VAR (COHERE_API_KEY) — gak ditulis ke file manapun.
"""
import csv
import json
import os
import sys
import time
import urllib.request

import numpy as np

BASE = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo"
MODEL = "embed-v4.0"
KEY = os.environ.get("COHERE_API_KEY")
assert KEY, "COHERE_API_KEY gak ada di env"

def embed(texts, input_type):
    out = []
    for i in range(0, len(texts), 48):
        chunk = [t[:2000] for t in texts[i:i+48]]
        req = urllib.request.Request(
            "https://api.cohere.com/v2/embed",
            data=json.dumps({"model": MODEL, "texts": chunk,
                             "embedding_types": ["float"], "input_type": input_type}).encode(),
            headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
            method="POST")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    out.extend(json.loads(r.read())["embeddings"]["float"])
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
    return np.array(out, dtype=np.float32)

def norm(a):
    n = np.linalg.norm(a, axis=1, keepdims=True)
    return a / np.maximum(n, 1e-9)

def cos(q, pool):
    return q @ pool

# ---------------- load data ----------------
rows = list(csv.DictReader(open(BASE + r"\data\sentiment\annotation_pilot.csv", encoding="utf-8")))
texts = [r["text"] for r in rows]
gold = [r["label"] for r in rows]
pred = [r["label_pred"] for r in rows]
pids = [r["postid"] for r in rows]
print(f"post: {len(rows)} | gold bull/bear/neutral/mixed = "
      f"{gold.count('bullish')}/{gold.count('bearish')}/{gold.count('neutral')}/{gold.count('mixed')}")

t0 = time.time()
E_doc = norm(embed(texts, "search_document"))
print(f"embed 150 post: {time.time()-t0:.0f}s, shape {E_doc.shape}")

# pool = gold beropini; query = pred neutral
pool_idx = [i for i in range(150) if gold[i] in ("bullish", "bearish")]
query_idx = [i for i in range(150) if pred[i] == "neutral"]
print(f"pool beropini: {len(pool_idx)} | query (pred neutral): {len(query_idx)}")

pool_v = E_doc[pool_idx]
pool_lab = [gold[i] for i in pool_idx]

def direction_eval(min_sim, k, loo=True):
    flips = correct = 0
    for qi in query_idx:
        q = E_doc[qi]
        sims = cos(q, pool_v.T)
        order = np.argsort(-sims)
        top = [(sims[j], pool_lab[j], pool_idx[j]) for j in order[:k]]
        if loo:
            top = [t for t in top if t[2] != pids[qi]][:k]
        if not top or top[0][0] < min_sim or len(top) < k:
            continue
        labs = {t[1] for t in top}
        if len(labs) != 1:
            continue
        flips += 1
        if labs.pop() == gold[qi]:
            correct += 1
    return flips, correct

print("\n--- direction (query = pred-neutral, gold = acuan) ---")
print("thr   k=3-unanim+LOO        k=1-top1")
for thr in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85):
    f3, c3 = direction_eval(thr, 3)
    f1, c1 = direction_eval(thr, 1, loo=False)
    p3 = f"{100*c3/f3:.0f}%" if f3 else "-"
    p1 = f"{100*c1/f1:.0f}%" if f1 else "-"
    print(f"{thr:.2f}  flip {f3:>2} benar {c3:>2} presisi {p3:>4}   flip {f1:>2} benar {c1:>2} presisi {p1:>4}")

# baseline: tebak bullish semua utk query yg flip-able? pakai rasio gold
golds_q = [gold[i] for i in query_idx]
nb = golds_q.count("bullish") / max(1, len(golds_q))
print(f"(baseline tebak-bullish utk seluruh query: {100*nb:.0f}%)")

# ---------------- kNN full-label LOO ----------------
knn_ok = 0
for i in range(150):
    if gold[i] not in ("bullish", "bearish", "neutral"):
        continue
    sims = E_doc @ E_doc[i]
    sims[i] = -9
    top = np.argsort(-sims)[:5]
    labs = [gold[j] for j in top if gold[j] != "mixed"][:3]
    if not labs:
        continue
    guess = max(set(labs), key=labs.count)
    knn_ok += guess == gold[i]
ev = sum(1 for g in gold if g != "mixed")
print(f"\nkNN k=3 LOO (semua label): {knn_ok}/{ev} = {100*knn_ok/ev:.1f}%  (lexicon 56.7%, MiniLM 46.7%)")

# ---------------- news materiality ----------------
try:
    news = list(csv.DictReader(open(BASE + r"\data\news\annotation_pilot.csv", encoding="utf-8")))
    ntext = [(r.get("title") or "") + " " + (r.get("summary") or r.get("text") or "") for r in news]
    ngold = [r.get("label") for r in news]
    MATERIAL = ["emiten dinyatakan pailit oleh pengadilan",
        "saham delisting dan dicabut dari bursa efek",
        "restrukturisasi utang dan penundaan pembayaran kewajiban",
        "emiten gagal bayar obligasi dan kewajiban utang",
        "akuisisi merger penggabungan perusahaan besar",
        "perubahan direksi dan komisaris utama perusahaan",
        "mendapat kontrak proyek baru bernilai besar",
        "pembagian dividen tunai kepada pemegang saham",
        "rugi bersih besar dan kinerja keuangan memburuk",
        "laba bersih naik tajam kinerja keuangan menguat",
        "right issue penjualan saham baru mencari dana",
        "sanksi denda dari regulator OJK dan bursa",
        "kebakaran pabrik produksi berhenti",
        "penyelidikan fraud dugaan manipulasi laporan keuangan"]
    NONMATERIAL = ["kondisi indeks IHSG melemah menguat hari ini",
        "opini analis rekomendasi umum kondisi pasar",
        "berita ekonomi makro global suku bunga the fed",
        "edukasi investasi literasi keuangan untuk pemula",
        "perubahan komposisi indeks saham di bursa",
        "kurs rupiah menguat terhadap dolar AS"]
    mv = norm(embed(MATERIAL, "search_document"))
    nv = norm(embed(NONMATERIAL, "search_document"))
    tv = norm(embed(ntext, "search_document"))
    margins = (tv @ mv.T).max(1) - (tv @ nv.T).max(1)
    print("\n--- news margin (gold material:", ngold.count("material"), "/", len(ngold), ") ---")
    for thr in (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        acc = sum(((m >= thr) == (g == "material")) for m, g in zip(margins, ngold))
        print(f"  thr {thr:.2f}: benar {acc}/{len(ngold)}")
    # contoh margin material
    for i, g in enumerate(ngold):
        if g == "material":
            print(f"  [material] margin {margins[i]:+.3f}: {ntext[i][:60]!r}")
except FileNotFoundError:
    print("news CSV gak ada, skip")

# ---------------- spot check search ----------------
q = norm(embed(["pailit gak bisa bayar utang"], "search_query"))[0]
sims = E_doc @ q
top = np.argsort(-sims)[:3]
print("\n--- search spot check: 'pailit gak bisa bayar utang' ---")
for j in top:
    print(f"  {sims[j]:.3f} [{gold[j]:>7}] {texts[j][:70]!r}")
