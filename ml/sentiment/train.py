"""Training sentiment IndoBERT — pilot 150 baris.

Sesuai INDOBERT_IMPLEMENTATION_PLAN.md bag. 27/8.9:
- GroupKFold 5 by postid (anti-leakage: post yang sama gak boleh ada di train & val)
- 3 kelas: bearish / neutral / bullish (pilot: 'mixed' 3 baris dilebur ke neutral)
- Benchmark vs lexicon-v3 di OOF (out-of-fold) yang sama -> adil
- Model final dilatih ulang di semua 150 baris -> models/indobert-stockbit-sentiment-v1

Jalankan dari venv ML:
  .venv-ml/Scripts/python.exe ml/sentiment/train.py
"""
import argparse
import csv
import datetime
import json
import os
import random
import sys

import numpy as np
import torch
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLASSES = ["bearish", "neutral", "bullish"]
LABEL2ID = {c: i for i, c in enumerate(CLASSES)}
ID2LABEL = {i: c for c, i in LABEL2ID.items()}
MIXED_FOLD_TO = "neutral"  # pilot: mixed (3 baris, 2%) dilebur ke neutral


def fold_label(x):
    x = (x or "").strip().lower()
    return MIXED_FOLD_TO if x == "mixed" else x


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_rows(path):
    rows = []
    skipped = 0
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lab = fold_label(r.get("label"))
            text = (r.get("text") or "").strip()
            if lab not in LABEL2ID or not text:
                skipped += 1
                continue
            rows.append({
                "id": r["id"],
                "postid": r["postid"],
                "ticker": (r.get("ticker") or "").strip(),
                "text": text,
                "label": lab,
                "lexicon": fold_label(r.get("label_pred")),
            })
    return rows, skipped


def make_folds(rows, n_folds, seed):
    """GroupKFold by postid, deterministic (seeded shuffle + round robin)."""
    postids = sorted({r["postid"] for r in rows})
    rng = random.Random(seed)
    rng.shuffle(postids)
    return {pid: i % n_folds for i, pid in enumerate(postids)}


def encode(tokenizer, texts, labels, max_length):
    enc = tokenizer(texts, truncation=True, max_length=max_length,
                    padding="max_length", return_tensors="pt")
    return enc["input_ids"], enc["attention_mask"], torch.tensor(labels)


def train_one(tokenizer, model_name, train_rows, device, args, seed):
    set_seed(seed)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name, num_labels=len(CLASSES), id2label=ID2LABEL, label2id=LABEL2ID
    ).to(device)

    ids, mask, y = encode(tokenizer, [r["text"] for r in train_rows],
                          [LABEL2ID[r["label"]] for r in train_rows], args.max_length)
    ds = torch.utils.data.TensorDataset(ids, mask, y)
    dl = torch.utils.data.DataLoader(ds, batch_size=args.batch_size, shuffle=True,
                                     generator=torch.Generator().manual_seed(seed))

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total_steps = args.epochs * len(dl)
    scheduler = get_linear_schedule_with_warmup(
        optimizer, int(0.1 * total_steps), total_steps)

    model.train()
    for epoch in range(args.epochs):
        running = 0.0
        for batch in dl:
            optimizer.zero_grad()
            out = model(input_ids=batch[0].to(device),
                        attention_mask=batch[1].to(device),
                        labels=batch[2].to(device))
            out.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            running += float(out.loss)
        print(f"    epoch {epoch+1}/{args.epochs} loss={running/len(dl):.4f}", flush=True)
    return model


@torch.inference_mode()
def predict(model, tokenizer, rows, device, max_length):
    ids, mask, _ = encode(tokenizer, [r["text"] for r in rows],
                          [0] * len(rows), max_length)
    model.eval()
    preds, probs_all = [], []
    for i in range(0, len(rows), 32):
        out = model(input_ids=ids[i:i+32].to(device),
                    attention_mask=mask[i:i+32].to(device))
        p = torch.softmax(out.logits, dim=-1).cpu()
        preds += [int(x) for x in p.argmax(dim=-1)]
        probs_all += [t.tolist() for t in p]
    return preds, probs_all


def metrics(y_true, y_pred):
    acc = sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(y_true)
    p, r, f1, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=[0, 1, 2], zero_division=0)
    per_class = {CLASSES[i]: {"precision": round(float(p[i]), 3),
                              "recall": round(float(r[i]), 3),
                              "f1": round(float(f1[i]), 3),
                              "support": int(sup[i])} for i in range(3)}
    return {"accuracy": round(acc, 4),
            "macro_f1": round(float(np.mean(f1)), 4),
            "per_class": per_class,
            "confusion": confusion_matrix(y_true, y_pred, labels=[0, 1, 2]).tolist()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(BASE, "data", "sentiment", "annotation_pilot.csv"))
    ap.add_argument("--out", default=os.path.join(BASE, "models", "indobert-stockbit-sentiment-v1"))
    ap.add_argument("--base-model", default="indobenchmark/indobert-base-p2")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-length", type=int, default=256)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-final", action="store_true", help="CV benchmark saja, jangan save model final")
    args = ap.parse_args()

    rows, skipped = load_rows(args.data)
    rows.sort(key=lambda r: r["id"])
    print(f"data: {len(rows)} baris (skip {skipped}) | kelas: "
          f"{ {c: sum(1 for r in rows if r['label']==c) for c in CLASSES} }")
    if len(rows) < 50:
        sys.exit("data terlalu sedikit")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device} | base model: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)

    fold_of = make_folds(rows, args.folds, args.seed)
    oof = [None] * len(rows)      # prediksi model OOF
    oof_lex = [None] * len(rows)  # prediksi lexicon di baris yang sama
    fold_f1s = []

    for k in range(args.folds):
        tr = [r for r in rows if fold_of[r["postid"]] != k]
        va = [r for i, r in enumerate(rows) if fold_of[r["postid"]] == k]
        print(f"\n=== fold {k+1}/{args.folds}: train {len(tr)} / val {len(va)} "
              f"({ {c: sum(1 for r in va if r['label']==c) for c in CLASSES} })")
        model = train_one(tokenizer, args.base_model, tr, device, args, args.seed + k)
        preds, _ = predict(model, tokenizer, va, device, args.max_length)
        fm = metrics([LABEL2ID[r["label"]] for r in va], preds)
        fl = metrics([LABEL2ID[r["label"]] for r in va],
                     [LABEL2ID[r["lexicon"]] for r in va])
        fold_f1s.append((fm["macro_f1"], fl["macro_f1"]))
        print(f"    val macro F1: model={fm['macro_f1']:.3f} vs lexicon={fl['macro_f1']:.3f}")
        for local_j, r in enumerate(va):
            global_j = next(idx for idx, rr in enumerate(rows) if rr is r)
            oof[global_j] = preds[local_j]
            oof_lex[global_j] = LABEL2ID[r["lexicon"]]
        del model

    y_true = [LABEL2ID[r["label"]] for r in rows]
    m_model = metrics(y_true, oof)
    m_lex = metrics(y_true, oof_lex)
    m_maj = metrics(y_true, [LABEL2ID["neutral"]] * len(rows))

    print("\n" + "=" * 60)
    print(f"HASIL OOF ({len(rows)} baris, {args.folds}-fold group-by-postid)")
    print("=" * 60)
    print(f"IndoBERT ({args.base_model}) : acc={m_model['accuracy']:.1%} macroF1={m_model['macro_f1']:.3f}")
    print(f"lexicon-v3 (baseline)        : acc={m_lex['accuracy']:.1%} macroF1={m_lex['macro_f1']:.3f}")
    print(f"majority (semua neutral)     : acc={m_maj['accuracy']:.1%} macroF1={m_maj['macro_f1']:.3f}")
    print("\nIndoBERT per kelas:")
    for c, m in m_model["per_class"].items():
        print(f"  {c:10s} P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f} (n={m['support']})")
    print("\nconfusion IndoBERT (baris=gold, kolom=pred; urutan bearish,neutral,bullish):")
    for i, row in enumerate(m_model["confusion"]):
        print(f"  {CLASSES[i]:10s} {row}")
    beats = m_model["macro_f1"] > m_lex["macro_f1"]
    print(f"\nKESIMPULAN: IndoBERT {'MENANG' if beats else 'KALAH'} vs lexicon-v3 "
          f"({m_model['macro_f1']:.3f} vs {m_lex['macro_f1']:.3f})")

    if args.no_final:
        print("\n(--no-final: model final gak disave)")
        return

    print(f"\nLatih model final di semua {len(rows)} baris...")
    model = train_one(tokenizer, args.base_model, rows, device, args, args.seed)
    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    meta = {
        "model_version": "indobert-stockbit-sentiment-v1",
        "base_model": args.base_model,
        "classes": CLASSES,
        "mixed_folded_to": MIXED_FOLD_TO,
        "trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "trained_rows": len(rows),
        "data_file": os.path.basename(args.data),
        "hyperparams": {"epochs": args.epochs, "batch_size": args.batch_size,
                        "lr": args.lr, "max_length": args.max_length,
                        "seed": args.seed, "folds": args.folds},
        "oof_metrics": m_model,
        "lexicon_baseline_oof": m_lex,
        "majority_baseline_oof": m_maj,
        "beats_lexicon": beats,
        "note": "Pilot 150 baris single-annotator (agent telaah). Benchmark OOF group-by-postid. "
                "Belum dikalibrasi; jangan anggap model_score sebagai confidence kalibrasi.",
    }
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"model final disave: {args.out}")


if __name__ == "__main__":
    main()
