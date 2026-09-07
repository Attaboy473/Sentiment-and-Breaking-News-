"""Materiality semantic — arketipe kejadian material (stdlib, Cohere embed-v4.0).

Nilai `materiality_margin(text)` = kedekatan makna artikel ke arketipe
kejadian material DIKURANGI kedekatan ke arketipe non-material
(cosine, Cohere embed-v4.0; input_type=search_document).

Threshold & kalibrasi (40 artikel pilot, 7 Sep):
  - Skala margin Cohere KOMPRES dibanding MiniLM (artikel material
    beneran: +0.03 .. +0.21) -> threshold 0.38 punya MiniLM GAK transfer.
  - Akurasi flat 37/40 di rentang 0.20-0.50 -> dipakai 0.20 (tengah zona,
    bukan tepi) buat sem_label.
  - Eskalasi by-context di app.py pakai ESCALATE_MARGIN = 0.20 (dikalibrasi
    ulang; MiniLM 0.55 dipensiunkan).
API mati / key gak ada -> None, pipeline rule jalan normal.
"""
import json
import os
import urllib.request

SIDECAR_URL = "http://127.0.0.1:8010"  # dipensiunkan — tinggal buat referensi

MATERIAL_ARCHETYPES = [
    "emiten dinyatakan pailit oleh pengadilan",
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
    "penyelidikan fraud dugaan manipulasi laporan keuangan",
]
NONMATERIAL_ARCHETYPES = [
    "kondisi indeks IHSG melemah menguat hari ini",
    "opini analis rekomendasi umum kondisi pasar",
    "berita ekonomi makro global suku bunga the fed",
    "edukasi investasi literasi keuangan untuk pemula",
    "perubahan komposisi indeks saham di bursa",
    "kurs rupiah menguat terhadap dolar AS",
]

# arketipe di-embed SEKALI per proses (dipanggil dari server yang long-lived)
_cache = None


def _normalize(v):
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def _embed(texts):
    """Embed Cohere + NORMALIZE (threshold 0.20 dikalibrasi di vektor unit)."""
    from ml.search import embed_cohere
    return [_normalize(v) for v in embed_cohere.embed_texts(texts, "search_document")]


def _arch_vectors():
    global _cache
    if _cache is None:
        mv = _embed(MATERIAL_ARCHETYPES)
        nv = _embed(NONMATERIAL_ARCHETYPES)
        _cache = (mv, nv)
    return _cache


def materiality_margin(text, summary=""):
    """Return float margin (material vs non-material), atau None kalau API mati."""
    try:
        mv, nv = _arch_vectors()
        v = _embed([(text + " " + (summary or ""))[:2000]])[0]
        ms = max(sum(a * b for a, b in zip(v, m)) for m in mv)
        ns = max(sum(a * b for a, b in zip(v, n)) for n in nv)
        return round(ms - ns, 4)
    except Exception:  # noqa: BLE001 — API mati / timeout -> None (fallback)
        return None
