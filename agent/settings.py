"""Static config shared across the agent package.

Service-level config (env-driven) lives in service/settings.py. This file
holds only constants needed by pure-function modules so they can be
imported without env loading.
"""

import os

# Data snapshot date — drives 'recent' windows. Real wall-clock time is not
# used because the simulator's customers are frozen to this date.
SIMULATED_NOW = os.environ.get("SIMULATED_NOW", "2026-04-13")

# Path to the read-only application DB.
APP_DB_PATH = os.environ.get("APP_DB_PATH", "app.db")

# Policy file + index location.
POLICY_FILE = os.environ.get("POLICY_FILE", "policy_and_faq.md")
POLICY_INDEX_PATH = os.environ.get("POLICY_INDEX_PATH", "policy_index.pkl")

# Audit DB
AUDIT_DB_PATH = os.environ.get("AUDIT_DB_PATH", "audit.db")
