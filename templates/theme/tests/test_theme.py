import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_theme_is_declared():
    manifest = json.loads((PLUGIN_DIR / "narranexus-plugin.json").read_text())
    assert manifest["frontend"]["ui"]["themes"] == ["__PLUGIN_ID__.theme"]
