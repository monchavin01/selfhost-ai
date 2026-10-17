#!/usr/bin/env python3
"""voices.py build|verify — sha256 manifest of the voice bank (wav + txt), `sha256sum` format."""
import hashlib
import os
import sys
from pathlib import Path

VOICES_DIR = Path(os.environ.get("VOICES_DIR", "/app/voices"))
MANIFEST = VOICES_DIR / "manifest.sha256"


def digests() -> dict[str, str]:
    files = sorted(p for p in VOICES_DIR.iterdir() if p.suffix in (".wav", ".txt"))
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def build() -> int:
    lines = [f"{h}  {name}\n" for name, h in digests().items()]
    MANIFEST.write_text("".join(lines))
    print(f"wrote {MANIFEST} ({len(lines)} files)")
    return 0


def verify() -> int:
    if not MANIFEST.is_file():
        print(f"missing {MANIFEST}", file=sys.stderr)
        return 1
    expected = {}
    for line in MANIFEST.read_text().splitlines():
        h, _, name = line.partition("  ")
        expected[name] = h
    actual = digests()
    bad = [f"{n}: {'missing' if n not in actual else 'hash mismatch'}" for n, h in expected.items() if actual.get(n) != h]
    bad += [f"{n}: not in manifest" for n in actual if n not in expected]
    for b in bad:
        print(b, file=sys.stderr)
    if not bad:
        print(f"voices ok ({len(expected)} files)")
    return 1 if bad else 0


if __name__ == "__main__":
    cmds = {"build": build, "verify": verify}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("usage: voices.py build|verify")
    sys.exit(cmds[sys.argv[1]]())
