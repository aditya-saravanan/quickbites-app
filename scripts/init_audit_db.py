"""Idempotent CREATE TABLE for the audit DB."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.settings import AUDIT_DB_PATH
from audit.store import AuditStore


def main() -> None:
    AuditStore(AUDIT_DB_PATH)
    print(f"Audit DB initialized at {AUDIT_DB_PATH}")


if __name__ == "__main__":
    main()
