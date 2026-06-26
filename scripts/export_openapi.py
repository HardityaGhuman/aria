"""
scripts/export_openapi.py
-------------------------
Freeze the live FastAPI schema to ``docs/api/openapi.json``. The React client
codegens its typed API client from this file, so a committed, stable contract is
the handoff boundary between backend and frontend.

Run: ``./venv/bin/python scripts/export_openapi.py`` (re-run after route changes).
"""
import json
import os
import sys

# Make the repo root importable whether run as a script or a module.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# A JWT secret is needed for config import paths; use a throwaway if unset since
# this only builds the schema (no server, no token signing).
os.environ.setdefault("JWT_SECRET", "export-only-not-a-real-secret-0123456789")

from backend.main import app  # noqa: E402

OUT_PATH = os.path.join(_ROOT, "docs", "api", "openapi.json")


def main() -> None:
    schema = app.openapi()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(schema, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"Wrote {OUT_PATH} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
