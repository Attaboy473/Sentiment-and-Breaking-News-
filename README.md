# MarketForge Demo — Sentiment & Breaking News Engine (IDX)

Demo pipeline analisis sentimen retail **Stockbit** + deteksi **breaking news** emiten Indonesia.
Satu perintah jalan, stdlib Python murni — kecerdasan makna (semantic) via **Cohere embed-v4.0**,
semua keputusan bisa diaudit.

> ⚠️ **DEMO / PROTOTIPE** — bukan sinyal beli-jual. Rekomendasi di board = baseline dummy.

---

## Apa yang dikerjain sistem ini

```
Stockbit stream ──► Collector ──► Sentiment Engine ──► Agregat per-ticker ──► UI Dashboard
RSS berita (IDX) ──► News Engine ────► Breaking Event ────► Flag stale rekomendasi
                              │
Semantic Layer (Cohere v4) ───┴──► 1. Arah post neutral (≈bullish/bearish)
                                   2. Materiality berita (sem_margin)
                                   3. Search post+berita by makna
```

### 1. Sentiment (per-cashtag)

| Lapisan | Engine | Kapan |
|---|---|---|
| Label utama | **lexicon-v3** — kamus + aturan konteks (negasi, intensifier) | semua post |
| **Direction resolver** | **Cohere kNN** ke 94 post berlabel (cosine ≥ 0.60, k=3 wajib sepakat, guard duplikat >0.99) | post yang rule nilai NEUTRAL |
| Abstain | dibiarkan neutral | bukti lemah / API mati |

Post yang ke-resolve dapat label `semantic-knn-v1` dan **ikut agregat** bull/bear per ticker.
Semua keputusan tercatat di tabel `semantic_direction_log` (arah, sim, provider).

**Benchmark (150 post berlabel, LOO + dedup): presisi 83%** di kode produksi
(12 flip, 10 benar) — vs tebak-bullish 33% dan MiniLM 60–67%.

Contoh nyata: `"$BBCA rungkad padahal banknya kek lintah ngisep darah"` → lexicon bilang
neutral → resolver: **≈ bullish** (klasik "harga turun, fondasi cuan" yang keyword engine
gak akan pernah nangkep).

### 2. Breaking News

| Lapisan | Engine | Kapan |
|---|---|---|
| **Trigger event** | **rule engine** — keyword material + prioritas sumber + recency + entity tiering (skor ≥45, severity HIGH/CRITICAL) | keputusan utama |
| Materiality semantic | **Cohere margin** — kedekatan makna ke 14 arketipe kejadian material (pailit, delisting, gagal bayar…) vs non-material → chip `SEM` di UI | semua artikel |
| **Eskalasi by-context** | rule 40–45 (hampir lolos) + margin ≥ 0.20 + keyword material + tidak sedang bantahan → naik tipis ke atas threshold (confidence −0.05) | artikel "hampir" |

Denial guard tetap jalan: *"X membantah kabar pailit"* **tidak** jadi event walau ada kata "pailit".

### 3. Search by makna

`/api/search?q=...` — dispatch otomatis:

- query ticker (`bbca`, `$BBCA BBRI`) → **literal** (urut kecocokan → likes → terbaru)
- query campuran (`bbca dividen`) → semantic + boost dokumen yang nyebut ticker
- query makna (`pailit gak bisa bayar utang`) → semantic penuh (nemu "tenggelam", "delisting")

Tanpa `COHERE_API_KEY`: fallback keyword otomatis (LIKE + skoring) — demo tetap jalan offline.

---

## Hasil benchmark (jujur, semua terukur di data sendiri)

| Uji | Hasil | Keputusan |
|---|---|---|
| IndoBERT fine-tune (5-fold) | **kalah lexicon 3/4 fold** (0.267 vs 0.524; 0.227 vs 0.646) | ❌ dibatalkan |
| kNN ganti-label penuh (LOO) | MiniLM 46.7%, Cohere 43.5% vs lexicon 56.7% | ❌ ditolak — embedding paham *topik*, bukan *stance* |
| Direction resolver (neutral→arah) | **Cohere 83%** vs MiniLM 60–67% vs baseline 33% | ✅ jalan di produksi |
| News materiality | Cohere 37/40 = setara lexicon | ✅ pendukung + eskalasi |
| Warna kartu gambar → arah post | 43% (warna chart = masa lalu, stance = masa depan) | ❌ ditolak |

Pola keputusan: **rule tetap raja; semantic dipakai persis di tempat terbukti menang.**

---

## Cara jalanin

```bash
# 1. (opsional, disarankan) set key Cohere — semantic aktif
export COHERE_API_KEY=isi_key_lu          # Windows bash; jangan ditulis ke file!

# 2. jalanin demo
python app.py                             # → http://localhost:8004
```

Tanpa key: jalan normal, search → keyword fallback, direction resolver → abstain.

**Index semantic** (sekali saja / saat data banyak berubah — butuh key):

```bash
.venv-ml/Scripts/python.exe scripts/build_embeddings_cohere.py   # index post+news
.venv-ml/Scripts/python.exe scripts/build_pool_cohere.py         # pool direction (94 post berlabel)
```

**Selftest**: `python app.py --selftest` → `SELFTEST: PASS`

---

## Endpoint utama

| Endpoint | Ket |
|---|---|
| `GET /` | Dashboard (rekomendasi, sentiment, news, events, chatter) |
| `GET /api/state` | State lengkap + mode + health |
| `POST /api/poll` | Poll Stockbit + RSS sekarang |
| `GET /api/chatter?ticker=X` | 12 post retail terbaru (+ arah semantic post neutral) |
| `GET /api/search?q=...` | Search post+berita (ticker/semantic/keyword otomatis) |
| `GET /api/event/{hash}` | Detail event + audit trail |
| `GET /api/health` | Status collector & RSS |
| `POST /api/reset` | Reset DB |

---

## Struktur

```
app.py                     # server stdlib + pipeline (collector, news, aggregate, API)
ml/search/
  embed_cohere.py          # client Cohere embed-v4.0 (stdlib urllib, key via env)
  chatter_direction.py     # resolver arah post neutral (Cohere kNN + abstain + audit)
  news_semantic.py         # materiality margin (14 arketipe)
  search.py                # engine search: literal / semantic / keyword fallback
scripts/
  build_embeddings_cohere.py   # index post+news → tabel embeddings
  build_pool_cohere.py         # pool post berlabel → tabel pool_vectors
  bench_cohere_embed.py        # benchmark + kalibrasi threshold
  bench_image_color.py         # benchmark warna kartu (ditolak, dokumentasi)
data/
  sentiment/annotation_pilot.csv   # 150 post berlabel (label + label pass-2)
  news/annotation_pilot.csv        # 40 artikel berlabel material
static/                    # UI vanilla JS (zero framework)
```

## Batasan (yang harus lu tau)

- Label acuan dibuat **satu annotator** (author repo) — bukan ground truth mutlak; 83% bisa geser ±10 poin di data baru.
- Sample pilot kecil (150 post / 40 artikel) — threshold bisa di-kalibrasi ulang seiring data nambah.
- Semantic **butuh internet + API key**; tanpa itu fitur makna off (demo tetap hidup, jadi rule murni).
- Semua tabel keputusan semantic (`semantic_direction_log`) tersedia buat audit — evaluasi ulang kapan aja.
