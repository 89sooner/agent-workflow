"""Small registered comparison command for the P0 evaluation."""
import json
from pathlib import Path
import sys

data = json.loads(Path("web/fixtures/views.json").read_text())
expected = {f"view-{i}": 29 for i in range(16)}
print(f"{sum(data.get(k) == v for k, v in expected.items())}/16 fixtures match")
sys.exit(0 if data == expected and sys.argv[1:] == ["--check"] else 1)
