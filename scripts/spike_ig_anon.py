"""Spike feasibility: Instaloader anonymous fetch profil publik IG (tanpa login)."""
import sys, traceback

import instaloader

L = instaloader.Instaloader(
    quiet=True,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
)

ACC = sys.argv[1] if len(sys.argv) > 1 else "stockalpha.id"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3

try:
    p = instaloader.Profile.from_username(L.context, ACC)
    print(f"OK profile: {p.username} | followers={p.followers} | posts={p.mediacount} | private={p.is_private}")
    got = 0
    for post in p.get_posts():
        cap = (post.caption or "")[:110].replace("\n", " / ")
        print(f"  [{post.date_local:%d %b %Y}] likes={post.likes} comments={post.comments} :: {cap}")
        got += 1
        if got >= N:
            break
    print("VERDICT: anonymous fetch WORKS" if got else "VERDICT: profile ok but no posts returned")
except Exception as e:
    print(f"FAIL {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)
