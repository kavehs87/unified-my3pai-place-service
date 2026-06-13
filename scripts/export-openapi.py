#!/usr/bin/env python3
"""Export FastAPI OpenAPI schema to docs/openapi.json."""
import json
import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dmo.main import app

spec = app.openapi()

out = Path(__file__).parent.parent / "docs" / "openapi.json"
out.write_text(json.dumps(spec, indent=2) + "\n")
print(f"Written to {out}")
