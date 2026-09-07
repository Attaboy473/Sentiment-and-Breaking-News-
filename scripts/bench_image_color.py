"""Benchmark warna kartu gambar stream vs arah post (READ-ONLY, gak ngubah DB).

Ambil post yang punya gambar DAN teksnya sudah jelas arahnya (lexicon-v3
non-neutral per-ticker), download gambarnya, hitung dominan warna
hijau/merah, bandingin sama arah teks. Kalau korelasi tinggi -> warna kartu
layak jadi bukti tambahan buat direction resolver.
"""
import json
import io
import sqlite3
import sys
import urllib.request

DB = r"C:\Users\USER\Desktop\Hermes\Random\marketforge_demo\demo.db"
MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
N_IMG = 14  # jangan rakus CDN

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute(
    """
    SELECT sp.postid, sp.raw_payload,
           COALESCE(spv.sentiment, sen.sentiment) AS sent
    FROM stream_posts sp
    LEFT JOIN sentiment_per_ticker spv
           ON spv.postid = sp.postid AND spv.model_version = 'lexicon-v3'
    LEFT JOIN sentiment_predictions sen
           ON sen.postid = sp.postid AND sen.model_version = 'lexicon-demo-v1'
    WHERE sp.raw_payload LIKE '%stream-asset.stockbit.com%'
    """).fetchall()
con.close()

from PIL import Image

def dominant(img):
    """Rasio piksel hijau vs merah (hue-based, area tengah biar fokus kartu)."""
    w, h = img.size
    crop = img.convert("RGB").resize((120, 120))
    px = crop.load()
    green = red = total = 0
    for y in range(120):
        for x in range(120):
            r, g, b = px[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 60 or mx - mn < 25:
                continue  # terlalu gelap / abu2 (background, teks putih)
            # hijau: G dominan; merah: R dominan dgn G rendah
            if g > r * 1.25 and g > b * 1.1:
                green += 1
            elif r > g * 1.35 and r > b * 1.2:
                red += 1
            total += 1
    return green / max(1, total), red / max(1, total)

tested = agree = 0
detail = []
for r in rows:
    if tested >= N_IMG:
        break
    sent = r["sent"]
    if sent not in ("bullish", "bearish"):
        continue
    try:
        p = json.loads(r["raw_payload"])
        urls = [u for u in (p.get("images") or []) if isinstance(u, str)][:1]
        if not urls:
            continue
        req = urllib.request.Request(urls[0], headers={"User-Agent": "Mozilla/5.0"})
        img = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=20).read()))
        g, rd = dominant(img)
        dom = "green" if g > rd * 1.3 else "red" if rd > g * 1.3 else "neutral"
        exp = "green" if sent == "bullish" else "red"
        tested += 1
        ok = dom == exp
        agree += ok
        detail.append((r["postid"], sent, dom, round(g, 2), round(rd, 2), ok))
    except Exception as e:
        detail.append((r["postid"], sent, f"ERR {str(e)[:40]}", 0, 0, False))

print(f"{'postid':>9} {'teks':>8} {'warna':>8} {'hijau':>6} {'merah':>6}  cocok")
for d in detail:
    print(f"{d[0]:>9} {d[1]:>8} {d[2]:>8} {d[3]:>6} {d[4]:>6}  {'OK' if d[5] else '--'}")
print(f"\nAGREE: {agree}/{tested}" + (f" = {100*agree/tested:.0f}%" if tested else ""))
