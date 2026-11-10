"""Copy only the participant UI assets to Vercel's public directory; no app imports."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("style.css", "tokens.css", "study.js")


def prepare_static():
    target = ROOT / "public" / "static"
    target.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(ROOT / "study" / "static" / name, target / name)


if __name__ == "__main__":
    prepare_static()
