"""Training skeleton materiality (dok 27/28). Butuh venv ML: pip install torch transformers datasets scikit-learn.
Jalankan HANYA setelah data/ annotation penuh + split train/validation/test.
Angka di bawah = starting point, bukan final (dok 8.7)."""
# from transformers import AutoModelForSequenceClassification, AutoTokenizer, Trainer, TrainingArguments
# from datasets import load_dataset
# from sklearn.metrics import f1_score

MODEL_NAME = "indobenchmark/indobert-base-p2"   # coba juga indolem/indobert-base-uncased
NUM_LABELS = 2
MAX_LENGTH = 384

def main():
    raise SystemExit(
        "Skeleton saja. Isi dataset di data/materiality/ (train.csv, validation.csv, test.csv) "
        "lalu ikutkan pola dari INDOBERT_IMPLEMENTATION_PLAN.md bag. 27. "
        "Best checkpoint by macro F1 di validation."
    )

if __name__ == "__main__":
    main()
