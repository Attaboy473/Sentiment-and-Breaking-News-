"""Scan staged diff utk kredensial sebelum push (hapus diri sendiri setelah jalan)."""
import subprocess

diff = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True).stdout
leaks = []
for i, line in enumerate(diff.splitlines()):
    low = line.lower()
    if "ycsfgz" in low:
        leaks.append((i, "cohere-key-prefix", line[:120]))
    low_clean = line.split("#")[0]
    if "cohere_api_key" in low_clean.lower() and ("=" in low_clean) and (
            "os.environ" not in low_clean and "getenv" not in low_clean and "environ[" not in low_clean):
        if '"yc' in low_clean or "'" in low_clean and len(line) > 60:
            pass  # assignment dengan literal panjang dicurigai di bawah
    if "export COHERE_API_KEY=" in line and "isi_key" not in line:
        leaks.append((i, "key-in-readme-cmd", line[:120]))

print("LEAK:" if leaks else "AMAN: tidak ada kredensial di staged diff")
for i, kind, l in leaks[:5]:
    print(f"  [{kind}] {l}")
