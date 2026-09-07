# Panduan Anotasi Pilot (ringkas dari INDOBERT_IMPLEMENTATION_PLAN.md 13.3/13.4)

Cara kerja: kolom `label` KOSONG = tugas lu. `label_pred` cuma saran mesin (lexicon-v3 /
rule proxy) - koreksi kalau salah. Isi `notes` kalau kasusnya menarik (sarcasm, ambigu).

## Sentiment (annotation_pilot.csv) - baca KONTEKS ticker-nya, bukan kata saja
- `bullish`  : ekspektasi positif (harga, perusahaan, peluang). Termasuk negasi yang membalik ("gak rugi" = positif).
- `bearish`  : ekspektasi negatif / warning / niat cut. "gak naik", "anjlok".
- `neutral`  : informasional, gak jelas stance (jadwal RUPS, berita netral).
- `mixed`    : stance positif DAN negatif substantif sekaligus ("fundamental bagus tapi mahal, tunggu koreksi").
- Post multi-ticker: nilainya per baris (per ticker), baca `target_context`.

## Materiality (news/annotation_pilot.csv) - "apakah ini bakal gerakkan harga secara berarti?"
- `material`      : nyentuh operasi / cash flow / solvency / ownership / struktur modal / regulasi / outlook laba / aksi korporasi besar.
- `non_material`  : berita rutin, komentar generik, BANTAHAN (denial), rumor belum konfirmasi, dampak dinyatakan tidak signifikan.
- Baris `bucket=hard_negative` paling berharga - labelin dengan teliti.

## Aturan umum
- Skeptis dengan label_pred: pred mesin salah justru data paling berharga.
- Jangan buang baris; ragu-ragu = neutral/non_material + catat di notes.
- Simpan file sebagai CSV (jangan ubah nama kolom).
