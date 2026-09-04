# MarketForge Demo — Sentiment & Breaking News

Demo **standalone** dari `MarketForge_Complete_Project_Bundle`: pipeline **sentiment komunitas Stockbit** + **breaking news engine** untuk emiten IDX, dalam **satu file Python tanpa dependency** (stdlib + SQLite saja). Satu perintah jalan.

> ⚠️ **Ini demo/edukasi, bukan saran investasi.** Sentiment = heuristik lexicon yang *belum dikalibrasi*, papan rekomendasi = baseline dummy, LLM validator = rule proxy. Semua mode kejujuran ini tampil di dalam app (health bar "Mode").

---

## Quick Start

```bash
python app.py              # jalan di http://127.0.0.1:8004/ (RSS breaking news aktif)
python app.py --no-rss     # tanpa RSS (hanya collector Stockbit)
python app.py --selftest   # 11 unit check tanpa nyalain server
```

Kebutuhan: **Python 3.10+** — tanpa pip, tanpa Docker, tanpa Postgres. Database SQLite (`demo.db`) dibuat otomatis.

---

## 1. Sentiment Komunitas Stockbit

### Cara datangnya
- **Collector** poll `GET https://exodus.stockbit.com/stream/non-login/symbol/{SYM}` — endpoint publik hasil riset feasibility (tanpa login, tanpa header khusus, window 30 post terakhir per ticker).
- 10 ticker watchlist dipoll bergiliran (stagger 1.5s), **dedupe by `postid`**, penulis dianonymisasi jadi `sb:<hash>` (username tidak pernah disimpan).
- **Fallback otomatis**: kalau API gagal, collector ambil 30 post yang sama dari halaman SSR `stockbit.com/symbol/{SYM}` via `__NEXT_DATA__` (temuan riset; error & status fallback transparan di health bar).
- **Filter noise**: post dari akun resmi emiten (`official`), repost riset broker (`isreport`), berita otomatis (`isnews`), dan post terlalu pendek (<12 char / emoji-only) **dibuang dari agregat** — jumlah yang dibuang tampil di kolom "Filter".

### Cara dinilai (lexicon v2, per-cashtag)
1. **Segmentasi per-cashtag** — post multi-ticker tidak lagi "sama rata". Teks dipecah per `$TICKER`: kata setelah `$TICKER` jadi milik ticker itu sampai cashtag berikutnya. Contoh: *"$BUMI gacor naik terus, $BBCA anjlok jeblok"* → BUMI **bullish**, BBCA **bearish**.
2. **Lexicon ±30 kata slang** (cuan, gacor, ara, sokong, gemoy / anjlok, jeblok, potong bongkar, bocor cc, gap down, dll).
3. **Normalisasi slang**: "naaaiiik" → naik, "anjloooq" → anjlok (termasuk leetspeak q→k), huruf berulang dirapikan.
4. **Intensifier** (banget/gila/parah/sangat…) memperbesar bobot 1.2–1.5×; **negator** 2 kata sebelum keyword (gak/ga/tidak/jangan/bukan…) membalik arah.
5. Skor = `(bull − bear) / (bull + bear)` → label **bullish** (≥ +0.34) / **bearish** (≤ −0.34) / neutral. Setiap skor disimpan dengan `model_version` (`lexicon-v2` per-ticker, `lexicon-demo-v1` whole-post) supaya ganti model tinggal re-score tanpa kolek ulang.

### Metrik agregat (per ticker, window 1 jam & 24 jam)
| Kolom | Arti |
|---|---|
| Mention | jumlah post (sudah lewat filter noise) |
| Bull/Bear | distribusi label |
| Net | net sentiment −100..+100 |
| Authors | author unik (hash) |
| Velocity | perubahan mention vs window sebelumnya — hanya dihitung kalau window sebelumnya ≥3 post (guard anti "plus 6500%") |
| Fresh | umur post terbaru (ijo ≤1 jam, kuning ≤3 jam, abu = basi) |
| Filter | jumlah post noise yang dibuang |
| Engage | bar engagement (1 + likes + replies) |

Ticker dengan sample <5 post ditandai **"sample tipis"** dan ditampilkan redup — jangan dinilai.

**Sektor Watchlist**: rollup tematik 24 jam (Bank, Energi, Logam & Mineral, Telko, Teknologi) dari post yang sudah terfilter — disclaimer: bukan klasifikasi board IDX resmi.

---

## 2. Breaking News Engine

### Filosofi: klasifikasi DAMPAK, bukan topik
Sumber: RSS **ANTARA Bursa & Finansial** (2 feed, poll tiap `DEMO_BREAKING_INTERVAL` detik). Semua artikel ≤48 jam disimpan & ditampilkan; yang berimbas tinggi jadi event.

**Rule score 0–100 yang explainable** (bisa dibongkar per alasan di modal audit trail):
- **Recency**: makin baru makin tinggi (hard cut: item >3 jam tidak jadi kandidat)
- **Source priority**: bobot kredibilitas feed
- **Entity**: ada nama emiten / sektor yang dikenali → naik
- **Impact keywords berbobot** (contoh): pailit 30 · suspensi/fraud 28 · delisting 26 · izin dicabut/ledakan 25 · korupsi/merger/produksi dihentikan 24 · akuisisi/kebakaran 22 · rights issue/kontrak jumbo 20 · buyback/MSCI/laba melonjak-anjlok 18 · dividen 14
- **HARD_EVENTS** (8 keyword paling parah: suspensi, pailit, gagal bayar, fraud, delisting, izin dicabut, produksi dihentikan, ledakan) → auto HIGH/CRITICAL

**Alur material event** (sesuai plan bundle §8):
1. Score **≥45** → kandidat event (badge CANDIDATE)
2. **HIGH/CRITICAL + ada emiten** → TRIGGERED → rekomendasi emiten terdampak di-flag **STALE** (bukan sinyal baru — cuma tanda "info lama sudah tidak valid")
3. **Targeted reassessment** disimulasikan ±20 detik → rekomendasi dapat skor & catatan baru, audit trail lengkap (klik kartu event: breakdown skor, reasons JSON, affected rekomendasi)

**News Hub** juga menampilkan: semua artikel dengan **skor relevansi** + kategori (Dividen/Lainnya) ala News Preview, chip filter, dan **Chatter Stockbit** (12 post retail terbaru per ticker, dengan link "Buka di Stockbit" ke post aslinya sebagai bukti data). Klik nama emiten di tabel sentiment → langsung lompat ke stream emiten itu.

---

## Parameter Utama

| Parameter | Default | Di mana |
|---|---|---|
| Watchlist | BBCA, BBRI, BMRI, TLKM, ANTM, INCO, ADRO, BUMI, GOTO, UNTR | `TICKERS` di `app.py` |
| Stagger antar ticker | 1.5 s | `collect_once()` |
| Breaking poll interval | 60 s (`DEMO_BREAKING_INTERVAL` env) | `BREAKING_INTERVAL` |
| Window berita tersimpan | 48 jam | `breaking_once()` |
| Threshold kandidat event | score ≥ 45 | `CANDIDATE_THRESHOLD` |
| Max umur item kandidat | 3 jam | `MAX_ITEM_AGE_MIN` |
| Retensi post | 14 hari (auto-prune tiap poll) | `collect_once()` |
| Window agregat | 1 jam & 24 jam | tab di UI |
| Minimal sample valid | 5 post | `sample_ok` |
| Guard velocity | prev window ≥3 post | `aggregate()` |
| Chatter per ticker | 12 post terbaru | `/api/chatter` |
| Artikel di News Hub | 40 terbaru | `build_state()` |
| Threshold label | bullish ≥ +0.34 / bearish ≤ −0.34 | lexicon v2 |

---

## Fitur UI

- Dua tampilan (drawer kanan, bisa keluar-masuk): **Sentiment** (papan rekomendasi + tabel komunitas + sektor) & **News** (News Hub + breaking events + chatter)
- Design language resmi MarketForge: canvas tenang, satu biru aksi, traffic-light pills, Inter, mono untuk angka, **tanpa emoji**, mode **terang/gelap** (persist di `localStorage["mf.theme"]`)
- Logo emiten dari CDN Stockbit; klik emiten → stream aslinya
- Toast, modal audit trail (tutup via X / klik luar / Esc), selftest, health bar real-time
- Reset DB dari UI (hapus data + seed ulang rekomendasi dummy)

---

## Tech Stack

| Layer | Teknologi |
|---|---|
| Backend | **Python stdlib saja**: `ThreadingHTTPServer`, `urllib`, `sqlite3`, `xml.etree`, `json`, `hashlib` |
| Database | SQLite (`demo.db`) — tabel: `stream_posts`, `stream_post_tickers`, `sentiment_predictions`, `sentiment_per_ticker`, `breaking_events`, `news_articles`, `recommendations`, `settings` |
| Frontend | Vanilla HTML/CSS/JS tanpa framework, design token dari `MarketForge-DESIGN.md` |
| Data | Stockbit stream non-login (+ fallback SSR `__NEXT_DATA__`), RSS ANTARA |

### API
| Endpoint | Method | Fungsi |
|---|---|---|
| `/api/state` | GET | Semua state UI (agregat, events, articles, chatter stats, health) |
| `/api/poll` | POST | Paksa poll Stockbit + RSS sekarang |
| `/api/simulate_event` | POST | Simulasi material event (body: `{"ticker": "ANTM"}`) |
| `/api/event/{hash}` | GET | Detail event + audit trail |
| `/api/chatter?ticker=X` | GET | 12 post retail terbaru emiten X (+ URL post asli) |
| `/api/health` | GET | Status collector & RSS |
| `/api/reset` | POST | Reset database |

---

## Struktur

```
marketforge_demo/
├── app.py            # backend: collector, sentiment v2, breaking engine, API (stdlib)
├── static/
│   ├── index.html    # layout 2 view + drawer + topbar
│   ├── style.css     # design token MarketForge (light/dark)
│   └── app.js        # state, render, interaksi
└── README.md
```

---

## Batasan & Etika (jujur, penting)

1. **Lexicon belum dikalibrasi** — bahasa stream sangat slang/sarkasme; sebelum dipercaya, rencananya labeling manual 300–500 post lalu benchmark IndoBERT vs LLM (sesuai implementation review bundle).
2. **Papan rekomendasi = dummy deterministik** (hash ticker) — cuma panggung untuk mendemokan alur STALE → reassess, bukan sinyal.
3. **LLM validator = rule proxy**; pipeline aslinya memakai LiteLLM.
4. Riset hanya **endpoint publik non-login** — tanpa bypass auth/anti-bot, tanpa kredensial, volume rendah. Username tidak disimpan (dihash).
5. Sentiment diposisikan sebagai **indikator konfirmasi**, bukan sinyal utama.
6. Bukan saran investasi.

Latar riset lengkap (feasibility endpoint, playbook, response schema) ada di repo `Attaboy473/stockbit-stream-feasibility`.
