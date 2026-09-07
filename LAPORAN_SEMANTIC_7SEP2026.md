# Laporan Implementasi: Dari IndoBERT ke Semantic Layer
**Proyek:** MarketForge Demo — Sentiment & Breaking News (IDX/Stockbit)
**Tanggal:** 7 September 2026
**Status akhir:** Semantic layer aktif (search + news scoring + direction hint); sentiment engine tetap lexicon-v3

---

## 1. Ringkasan Eksekutif

Hari ini kita mencoba menerapkan model bahasa (IndoBERT) untuk menggantikan engine sentiment berbasis lexicon. Prosesnya dijalankan **end-to-end secara nyata** (dataset → training → benchmark), dan hasilnya: **model kalah dari baseline rule-based yang sudah ada**, sehingga training dihentikan sebelum selesai.

Kemudian kita pivot ke pendekatan yang lebih ringan: **semantic embedding** (MiniLM multilingual, tanpa training). Pendekatan ini diuji di tiga titik dengan hasil berbeda:

| Uji coba | Hasil benchmark | Keputusan |
|---|---|---|
| IndoBERT fine-tuning (sentiment) | Kalah 3 dari 4 fold vs lexicon | **Dibatalkan** |
| Semantic kNN sebagai pengganti sentiment | 46.7% vs 56.7% (kalah) | **Tidak dipakai** |
| Semantic search (fitur pencarian) | Unggul untuk paraphrase/sinonim | **Diimplementasi penuh** |
| Semantic materiality (news) | Seri 37/40 dengan lexicon, error komplementer | **Dipakai sebagai sinyal pendukung** |
| Semantic direction hint (post neutral) | Presisi ~60% vs 33% tebakan naive | **Diimplementasi sebagai hint transparan** |

Prinsip yang dipegang sepanjang proses: **benchmark dulu di data berlabel, baru di-wire**. Apa yang kalah tidak dipaksakan; apa yang menang dipasang dengan batasan yang jujur.

---

## 2. Kondisi Awal (sebelum eksperimen)

Aplikasi demo berjalan sebagai single-file Python (**stdlib only** + SQLite) di port 8004:

- **Sentiment engine:** `lexicon-v3 per-cashtag + context rules` — kamus bull/bear per cashtag, bigram phrase matching, intent-flip ("gak rugi" → bullish), denial guard untuk RSS, entity resolver nama emiten (confidence ≥0.8 dapat bonus skor). Selftest 32/32 PASS.
- **Breaking news engine:** rule score 0–100 dari bobot keyword (pailit 30, dst.), threshold kandidat ≥45, alur event → STALE → reassess.
- **Keterbatasan yang sudah teridentifikasi** (dari telaah 150 post berlabel, bagian 3): akurasi lexicon 56.7% — blind spot utama adalah semangat beli/jual tanpa kata kamus ("gas gas gas", "bidnya dibikin tebel").

---

## 3. Tahap 1 — Dataset Pilot & Telaah Label

### 3.1 Pembuatan dataset
Dua file CSV anotasi di-generate dari database demo:

- `data/sentiment/annotation_pilot.csv` — **150 baris** post Stockbit, sampling campuran: 71 bucket `bull_bear`, 61 `no_hit`, 18 `negation`. Kolom `label_pred` sudah terisi prediksi lexicon-v3.
- `data/news/annotation_pilot.csv` — **40 baris** artikel RSS dengan kolom headline/summary/keywords/denial_hits.

Seluruh 190 baris diberi label manual (single-annotator). Kualitas data diverifikasi: 0 duplikat, 0 teks kosong, coverage 10 emiten.

### 3.2 Hasil telaah terhadap lexicon-v3 (baseline resmi)

**Sentimen (150 baris):**
- Akurasi keseluruhan: **56.7%** (85/150)
- Macro F1 4-kelas: **0.404**; 3-kelas (mixed→neutral): **0.534**
- Per bucket: negation 72.2% (context rules berfungsi), bull_bear 56.3%, no_hit 52.5%
- Blind spot struktural: **31 post bullish dianggap neutral** (slang tanpa keyword kamus), 11 false-positive dengan skor ekstrem (post jurnal rehash, data transaksi investor, berita emiten lain yang menyeret ticker)

**News materiality (40 baris):**
- Akurasi: **92.5%** (37/40). Error: 1 false negative (restrukturisasi utang), 1 hard negative sesuai desain (IPO pihak lain), 1 gray case.

**Kesimpulan tahap:** dataset layak jadi benchmark; baseline yang harus dikalahkan adalah macro F1 0.534 (3-kelas).

---

## 4. Tahap 2 — Infrastruktur ML

- Dibuat **venv terpisah** `.venv-ml` (Python 3.11): torch 2.14.0+cpu, transformers 5.16.1, scikit-learn 1.9.0. Alasan: demo utama dijanjikan tetap stdlib 1-perintah; ML tidak boleh menjadi dependensi wajib.
- **Kendala jaringan:** `pip install torch` dari index PyTorch CPU gagal ("versions: none") dan download via PyPI berjalan ~10 KB/s (cache diam 83MB selama 4 menit).
- **Solusi yang terbukti:** resolve dependensi via `pip install --dry-run --report` (40 wheel), download semua wheel paralel dengan curl 8 slot (206 MB dalam ±2 menit), lalu install offline `pip install --no-index --find-links`. 
- Base model `indobenchmark/indobert-base-p2` (498 MB) di-download langsung dari HuggingFace ke `models/_hf_base/` dan diverifikasi bisa di-load + inference (logits shape benar, classifier head baru = wajar karena task berbeda).

---

## 5. Tahap 3 — Training IndoBERT (Dibatalkan Berdasarkan Data)

### 5.1 Desain (mengikuti INDOBERT_IMPLEMENTATION_PLAN.md)
- 3 kelas: bearish / neutral / bullish (mixed 3 baris dilebur ke neutral)
- **5-fold GroupKFold by postid** (anti-leakage; ternyata 150 postid unik semua, fold balance 30/30/30/30/30, semua kelas terwakili)
- Hyperparameter: epochs 4, batch 16, lr 2e-5, max_length 256, seed 42
- Benchmark OOF (out-of-fold) dibandingkan langsung dengan lexicon-v3 pada baris yang sama + baseline majority
- Model final direncanakan disimpan ke `models/indobert-stockbit-sentiment-v1` dengan meta.json

### 5.2 Hasil per fold (macro F1, validasi 30 baris)

| Fold | IndoBERT | lexicon-v3 | Selisih |
|---|---|---|---|
| 1 | **0.484** | 0.450 | model menang |
| 2 | 0.267 | **0.524** | model kalah telak |
| 3 | 0.227 | **0.646** | model kalah telak |
| 4 | 0.422 | **0.478** | model kalah |
| 5 | — (dihentikan) | — | — |

Loss training menurun normal (1.12 → 0.75), artinya model belajar — tapi generalisasi buruk. Waktu: **±90 detik/epoch di CPU** (total ±45 menit untuk CV penuh).

### 5.3 Keputusan
Training di-**abort** di fold 5. Alasan: (a) kalah 3/4 fold dengan margin besar, (b) variance antar-fold tinggi = tanda klasik data terlalu kecil (120 baris train/fold), (c) biaya komputasi tidak sebanding. Proses dibunuh bersih; tidak ada checkpoint yang tertinggal.

**Pelajaran:** fine-tuning transformer butuh minimal 500–1000 label untuk task slang campuran ini; 150 baris hanya cukup sebagai pilot benchmark.

---

## 6. Tahap 4 — Pivot: Semantic Search (BERHASIL, DIIMPLEMENTASI)

### 6.1 Arsitektur
Model: **`paraphrase-multilingual-MiniLM-L12-v2`** (470 MB, gratis, lokal, mean pooling 384-dim). Dipilih karena multilingual (paham Indonesia + slang), tanpa training, ringan untuk CPU.

```
┌─────────────────────────┐
│ venv ML: sidecar :8010  │  ml/search/server.py
│ POST /embed → 384-dim   │  (torch, long-lived, arketipe di-cache)
└───────────▲─────────────┘
            │ urllib (stdlib)
┌───────────┴─────────────┐
│ app.py (stdlib) :8004   │  /api/search
│ cosine vs tabel         │  fallback keyword bila sidecar mati
│ `embeddings` demo.db    │
└─────────────────────────┘
```

- **Indexer** `scripts/build_embeddings.py`: encode semua post + berita ke tabel `embeddings` (incremental, re-run aman). Hasil: **1.466 post + 13 berita** ter-encode dalam beberapa menit.
- **Search engine** `ml/search/search.py`: mode `semantic` (cosine) dengan fallback `keyword` otomatis — janji "demo stdlib tetap jalan tanpa ML" tidak pecah.
- **UI:** search box di topbar + view "Hasil Pencarian" (badge POST/BERITA, skor kemiripan %).

### 6.2 Bukti kerja (A/B keyword vs semantic, korpus sama)
- Query `"deviden"` → keyword menang (ejaan persis ada di korpus, skor 1.005).
- Query `"saham turun semua busuk"` → semantic menang: menangkap post yang menulis "sampah", "ampas" (sinonim tanpa kata sama).
- Query `"saham pailit gak bisa bayar utang"` → semantic menangkap konteks "delisting"/"tenggelam".

Health endpoint: `"search": {"mode": "semantic (MiniLM multilingual via sidecar :8010)", "indexed": 1479}`.

---

## 7. Tahap 5 — Semantic untuk Sentiment (DIUJI → DIPAKAI SEBAGIAN)

### 7.1 kNN sebagai pengganti label: KALAH
Leave-one-out di 150 post berlabel: tetangga terdekat menentukan label.

| Konfigurasi | Akurasi | Macro F1 |
|---|---|---|
| **Baseline lexicon-v3** | **56.7%** | **0.534** |
| kNN k=7 weighted (terbaik) | 46.7% | 0.456 |
| Hybrid lexicon-first + kNN | 48.7% | 0.496 |

**Insight teknis:** embedding paraphrase menangkap **topik**, bukan **stance** — post "naik terus" dan "turun terus" sangat mirip secara makna topik (sama-sama tentang pergerakan harga) tetapi berlawanan arah. Untuk klasifikasi arah sentimen, ini fatal. (Eksperimen mikro-lexicon tambahan juga gagal: 56.0% — kata gaul terlalu ambigu tanpa konteks; konfirmasi ceiling rule-based.)

### 7.2 Direction hint untuk post NEUTRAL: JALAN, DIPASANG
Kebutuhan user: post yang labelnya neutral terlalu "mati" — butuh indikasi arah. Solusi yang terbukti dari grid search gate ketat:

| Konfigurasi | Flip | Arah benar | Presisi |
|---|---|---|---|
| sim ≥ 0.75, k=3, wajib sepakat | 10 | 6 | **60%** |
| sim ≥ 0.80, k=3, wajib sepakat | 4 | 2 | 100% (sampel kecil) |
| (naive: tebak bullish semua) | 64 | 31 | 33% |

**Implementasi** (`ml/search/chatter_direction.py` + tabel `labeled` 150 baris, pool 83 post beropini jelas):
- Post neutral dicocokkan ke pool; jika 3 tetangga (cosine ≥ 0.75) **sepakat arah** → tampil hint: `≈ bullish · mirip 78% · contoh: "..."` lengkap dengan bukti post pendukung.
- **Tidak mengganti label** — agregat sentiment tetap dari lexicon-v3; hint bersifat transparan (hover menjelaskan metodologi).
- Terbukti live: post "Ga pengen kah nembus 70..." (neutral) mendapat hint `≈ bullish 0.78`; post benar-benar campuran arah tidak diberi hint.

### 7.3 Keputusan sentiment akhir
Engine keputusan = **lexicon-v3 (tetap)** + direction hint semantic pada post neutral. Mode line di health endpoint jujur mencantumkan: "semantic kNN diuji & kalah → tidak dipakai".

---

## 8. Tahap 6 — Semantic untuk Breaking News (PENDUKUNG)

### 8.1 Benchmark
14 arketipe material (pailit, delisting, restrukturisasi utang, gagal bayar, akuisisi, kontrak besar, dividen, fraud, dsb.) vs 6 arketipe non-material, di-embed; skor artikel = margin cosine.

- **Semantic: 37/40** (threshold terbaik 0.38) — **sama dengan lexicon: 37/40**
- Error-nya **komplementer**: semantic benar menolak hard-negative "IPO UGM" (yang lexicon salah terima), tetapi ikut meleset di "restrukturisasi utang Waskita"
- Catatan jujur: threshold dikalibrasi di 40 baris yang sama → risiko overfit tinggi

### 8.2 Keputusan: sinyal pendukung, bukan penentu trigger
Karena seri (bukan menang), trigger breaking event **tidak diubah**. Yang diimplementasi:

- `ml/search/news_semantic.py` — `materiality_margin(title, summary)` dihitung saat setiap artikel masuk (ingest); sidecar mati → `NULL`, pipeline normal.
- Kolom baru `sem_margin`, `sem_label` di tabel `news_articles` (migration idempotent; backfill 13/13 artikel lama sukses).
- UI: chip `SEM +0.12 material` pada setiap kartu artikel, dengan tooltip penjelasan.
- Nilai strategis: data margin terakumulasi otomatis untuk kalibrasi trigger berbasis semantic di masa depan, saat label sudah cukup.

---

## 9. Kondisi Akhir Sistem

| Segmen | Engine keputusan | Lapisan semantic |
|---|---|---|
| Sentiment label | lexicon-v3 per-cashtag + context rules | Direction hint pada post neutral (kNN ke 83 post beropini, gate ketat) |
| Breaking news trigger | rule score ≥45 (tidak berubah) | `sem_margin` per artikel sebagai sinyal pendukung + audit |
| Pencarian | — (fitur baru) | **Semantic penuh** (cosine 384-dim atas 1.479 dokumen) |
| Fallback | — | Sidecar mati → keyword search / tanpa hint / margin NULL; demo stdlib tetap berjalan penuh |

Verifikasi: selftest 32/32 PASS, `node --check` OK, semua asset 200, poll collector berjalan normal (4 post baru, 1 event trigger via jalur rule lama), 13/13 artikel ter-backfill margin, chatter BUMI menunjukkan hint semantic live.

---

## 10. Keterbatasan (Kejujuran Metodologi)

1. **Single-annotator** — semua 190 label dibuat satu pihak (agent); belum ada ukuran inter-annotator agreement.
2. **Sample kecil** — presisi hint 60% dihitung dari 10 flip; threshold news 0.38 dikalibrasi in-sample (overfit risk).
3. **Embedding ≠ stance** — MiniLM efektif untuk kemiripan topik/search, tidak untuk arah sentimen.
4. **Belum di-push** — seluruh pekerjaan (Phase 1–3 + semantic layer) masih lokal; remote GitHub masih versi pra-Phase-1.
5. IndoBERT p2 (498 MB) masih tersimpan di `models/_hf_base/` — dapat dihapus bila tidak akan dilanjutkan.

---

## 11. Inventaris File & Cara Menjalankan

**File baru hari ini:**
```
ml/search/server.py              # sidecar embedding HTTP :8010 (venv ML)
ml/search/search.py              # engine search (semantic + fallback keyword)
ml/search/news_semantic.py       # materiality margin via arketipe
ml/search/chatter_direction.py   # direction hint untuk post neutral
scripts/build_embeddings.py      # indexer incremental → tabel embeddings
models/_hf_base/                 # MiniLM (aktif) + IndoBERT p2 (cadangan)
data/sentiment/annotation_pilot.csv   # 150 baris, terisi label
data/news/annotation_pilot.csv        # 40 baris, terisi label
```

**File yang dimodifikasi:**
```
app.py                    # /api/search, _search_mode(), kolom sem_margin/sem_label,
                          # hint di /api/chatter, mode line jujur
static/index.html         # search box topbar + view hasil pencarian
static/app.js             # renderSearch, doSearch, chip SEM, hint chatter
static/style.css          # styling search + chip + hint
README.md                 # endpoint + materiality semantic
ml/sentiment/train.py     # training lengkap (5-fold CV + benchmark) — terdokumentasi
ml/sentiment/infer.py     # perbaikan format input = format training
```

**Cara menjalankan:**
```bash
# Terminal 1 — sidecar embedding (biarkan hidup)
.venv-ml/Scripts/python.exe ml/search/server.py

# Terminal 2 — aplikasi demo (seperti biasa)
python app.py               # → http://127.0.0.1:8004/

# Setelah poll post baru — refresh index (incremental, cepat)
.venv-ml/Scripts/python.exe scripts/build_embeddings.py
```

---

## 12. Langkah Lanjut yang Disarankan

1. **Perbanyak label** (target 500–1000): otomatis meningkatkan pool hint DAN membuka jalan ulang IndoBERT/kNN sentiment dengan data layak.
2. **Kalibrasi trigger news berbasis margin**: begitu arsip artikel berlabel cukup (JSONL archive sudah berjalan), uji apakah margin semantic menaikkan presisi trigger — baru wire ke jalur event.
3. **Inter-annotator agreement**: minta anotator kedua untuk subset 50 baris guna mengukur kualitas label.
4. **Push ke GitHub**: seluruh perubahan (Phase 1–3 correctness/security, entity resolver, semantic layer) belum ada di remote.
5. Bersihkan `models/_hf_base/indobert-base-p2` (498 MB) bila jalur fine-tuning tidak dilanjutkan dalam waktu dekat.
