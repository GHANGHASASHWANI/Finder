#!/usr/bin/env python3
"""Keeps the private files encrypted in the public repo.

  python secure.py keygen   # print a new key -> save it as the FINDER_KEY secret (and in your password manager)
  python secure.py lock     # encrypt private files  ->  <file>.enc   (commit these)
  python secure.py unlock   # decrypt <file>.enc     ->  <file>       (never commit these)

The key is read from the FINDER_KEY environment variable.
"""
import os
import sys

from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.abspath(__file__))
PRIVATE = ["resume.json", "companies.json", "state/seen.json", "state/companies.json", "state/status.json", "state/ai.json"]


def key():
    k = os.environ.get("FINDER_KEY", "").strip()
    if not k:
        sys.exit("FINDER_KEY is not set")
    return Fernet(k.encode())


def lock():
    f = key()
    for rel in PRIVATE:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        with open(p, "rb") as src:
            data = src.read()
        try:  # skip unchanged files so the repo doesn't get a new commit every run
            with open(p + ".enc", "rb") as old:
                if f.decrypt(old.read()) == data:
                    continue
        except Exception:
            pass
        # never overwrite a newer encrypted copy (e.g. state pulled from GitHub) with an older local file
        if os.path.exists(p + ".enc") and os.path.getmtime(p) < os.path.getmtime(p + ".enc"):
            print("skipped", rel, "(encrypted copy is newer - run 'python3 secure.py unlock' first)")
            continue
        with open(p + ".enc", "wb") as dst:
            dst.write(f.encrypt(data))
        print("locked", rel)


def unlock():
    f = key()
    for rel in PRIVATE:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p + ".enc"):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p + ".enc", "rb") as src, open(p, "wb") as dst:
                dst.write(f.decrypt(src.read()))
            print("unlocked", rel)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "keygen":
        print(Fernet.generate_key().decode())
    elif cmd == "lock":
        lock()
    elif cmd == "unlock":
        unlock()
    else:
        sys.exit(__doc__)
