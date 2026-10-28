from pathlib import Path

from narranexus.sdk import BundleSpec, Contribution

_HERE = Path(__file__).resolve().parent.parent / "bundles"


def _spec() -> BundleSpec:
    import hashlib

    path = _HERE / "team.nxbundle"
    return BundleSpec("__PLUGIN_ID__.team", path, hashlib.sha256(path.read_bytes()).hexdigest(), "__DISPLAY_NAME__ team", "A team template")


# Contribution ids are global within a slot (two plugins named "team" cannot coexist): name yours after the plugin.
BUNDLES = (Contribution("__PLUGIN_PKG___team", _spec),)


def activate(ctx):
    pass
