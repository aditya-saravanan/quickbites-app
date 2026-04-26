"""One-shot: chunk + embed policy_and_faq.md, persist to policy_index.pkl."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.policy_index import build_or_load_index
from agent.settings import POLICY_FILE, POLICY_INDEX_PATH


def main() -> None:
    idx = build_or_load_index(POLICY_FILE, POLICY_INDEX_PATH)
    print(f"Built policy index: {len(idx.chunks)} chunks → {POLICY_INDEX_PATH}")
    for c in idx.chunks:
        print(f"  - {c.heading} ({len(c.text)} chars)")


if __name__ == "__main__":
    main()
