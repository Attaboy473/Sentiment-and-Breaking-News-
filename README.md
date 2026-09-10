# MarketForge Demo — Sentiment & Breaking News Engine (IDX)

Pipeline analisis sentimen retail **Stockbit** + deteksi **breaking news** emiten Indonesia.
Satu perintah jalan, stdlib Python murni. Semantic layer: **Cohere embed-v4.0**.

> **DEMO / PROTOTIPE** — bukan sinyal beli-jual. Rekomendasi di board = baseline dummy.

```
Stockbit stream ──► Collector ──► Sentiment Engine ──► Agregat per-ticker ──► Dashboard
RSS berita (IDX) ──► News Engine ────► Breaking Event ────► Flag stale rekomendasi
                         │
Semantic Layer (Cohere) ─┴─► 1. Arah post neutral (two-tier kNN)
                             2. Materiality berita (sem_margin)
                             3. Search post+berita by makna
```

## Sentiment (per-cashtag)

| Lapisan | Engine |
|---|---|
| Label utama | **lexicon-v4** — kamus slang retail + **sinyal emoji** (🚀 bull, 😭🥶💸 bear) + **digest-guard** (jurnal/quote-card → neutral) + negasi/intensifier |
| Direction resolver | **Cohere kNN two-tier** ke 94 post berlabel: `high` = k=3 sepakat ≥0.60, `medium` = k=1 ≥0.70 (skor ×0.8). Zona 0.60–0.70 = **abstain** (kalibrasi produksi: presisinya cuma 56–65%) |
| Guard | dedup >0.99, audit tiap flip ke `semantic_direction_log` (+provider, +tier) |

Post yang ke-resolve berlabel `semantic-knn-v1` dan ikut agregat bull/bear per ticker.

**Eval end-to-end** (147 post berlabel, recompute live): **69% akurasi** (dari 56% sebelum
lexicon-v4) — lexicon 73%, resolver 91%, abstain 61%. Presisi resolver saat bicara: 89%.

## Breaking News

| Lapisan | Engine |
|---|---|
| Trigger | rule engine — keyword material + prioritas sumber + recency + entity tiering (≥45) |
| Materiality | Cohere margin ke 14 arketipe kejadian material (thr 0.20 skala Cohere) |
| Eskalasi | rule 40–45 + margin ≥0.20 + keyword material + tanpa bantahan → naik tipis |

Denial guard: *"X membantah kabar pailit"* **tidak** jadi event.

## Search by makna

`/api/search?q=...` dispatch otomatis: ticker → literal, campuran → semantic + boost,
makna murni → semantic penuh. Tanpa `COHERE_API_KEY`: fallback keyword, demo tetap jalan.

## Benchmark (semua diukur, bukan klaim brosur)

| Uji | Hasil | Keputusan |
|---|---|---|
| IndoBERT fine-tune (5-fold) | kalah lexicon 3/4 fold | ❌ dibatalkan |
| kNN ganti-label penuh (LOO) | Cohere 43.5% / MiniLM 46.7% vs lexicon 56.7% | ❌ ditolak |
| Direction resolver (two-tier) | **89% presisi** saat flip; e2e 56→**69%** | ✅ produksi |
| News materiality | Cohere 37/40, setara rule | ✅ pendukung |
| Warna kartu gambar → arah | 43% < koin | ❌ ditolak |
| Arbitration kNN×lexicon | nangkep 0–1 dari 15 error lexicon | ❌ ditolak |
| Instagram source | API 429 / feed 401 require_login (anon) | ❌ spike stop |

Pola keputusan: **rule tetap raja; semantic dipakai persis di tempat terbukti menang.**

## Cara jalanin

```bash
# 1. set key Cohere (env var, jangan ke file) — tanpa ini: fallback keyword + abstain
export COHERE_API_KEY=isi_key_lu

# 2. jalanin
python app.py                             # → http://localhost:8014
```

Port default **8014** (8004 dikuasai Docker BE stack MarketForge production). Ganti via `DEMO_PORT`.

Index semantic (sekali saja / data banyak berubah, butuh key):

```bash
.venv-ml/Scripts/python.exe scripts/build_embeddings_cohere.py   # index post+news
.venv-ml/Scripts/python.exe scripts/build_pool_cohere.py         # pool direction (94 post)
```

Selftest: `python app.py --selftest` → `SELFTEST: PASS`

## Endpoint

| Endpoint | Ket |
|---|---|
| `GET /` | Dashboard |
| `GET /api/state` | State + mode + health |
| `POST /api/poll` | Poll Stockbit + RSS sekarang |
| `GET /api/chatter?ticker=X` | 12 post retail terbaru (+ arah semantic) |
| `GET /api/search?q=...` | Search ticker/semantic/keyword |
| `GET /api/event/{hash}` | Detail event + audit |
| `GET /api/health` | Status collector & RSS |
| `POST /api/reset` | Reset DB |

## Struktur

```
app.py                       # server stdlib + pipeline lengkap
ml/search/
  embed_cohere.py            # client Cohere embed-v4.0 (stdlib, key via env)
  chatter_direction.py       # resolver two-tier (Cohere kNN + abstain + audit)
  news_semantic.py           # materiality margin (14 arketipe)
  search.py                  # literal / semantic / keyword fallback
scripts/
  build_embeddings_cohere.py # index post+news → embeddings
  build_pool_cohere.py       # pool berlabel → pool_vectors
  eval_end2end_v3.py         # eval akurasi end-to-end live (147 pilot)
  test_two_tier.py           # validasi two-tier di 56 post uji
  calib_medium_tier.py       # kalibrasi threshold tier medium per-bucket sim
  bench_cohere_embed.py      # benchmark Cohere vs MiniLM
  bench_image_color.py       # benchmark warna kartu (ditolak, dokumentasi)
data/
  sentiment/annotation_pilot.csv   # 150 post berlabel (+ pass-2)
  news/annotation_pilot.csv        # 40 artikel berlabel material
static/                      # UI vanilla JS
```

## Batasan

- Label acuan **satu annotator** — bukan ground truth mutlak; angka bisa geser ±10 poin di data baru.
- Pilot kecil (150 post / 40 artikel) — threshold di-kalibrasi ulang seiring data tumbuh.
- Semantic butuh internet + key; tanpa itu fitur makna off (demo tetap hidup, rule murni).
- Sisa error terbesar: abstain-band (sarkasme, slang hiper-lokal, multi-ticker ambigu) — jalur perbaikannya active learning, bukan tuning manual.
