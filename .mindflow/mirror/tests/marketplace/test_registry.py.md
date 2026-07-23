---
code_file: tests/marketplace/test_registry.py
last_verified: 2026-07-22
stub: false
---

# test_registry.py

RegistryService + LocalMarketplaceSource end-to-end on a LocalArtifactStore:
publish writes catalog/scan/artifact; malicious publish raises
PublishRejectedError and leaves no catalog row; version is mandatory;
check_updates semver compare; marketplace install stamps
source_type=marketplace and bumps downloads; the tamper test overwrites the
stored artifact after publish and asserts the pipeline's hash verify
aborts; recursive dependency install pulls base-skill automatically.
