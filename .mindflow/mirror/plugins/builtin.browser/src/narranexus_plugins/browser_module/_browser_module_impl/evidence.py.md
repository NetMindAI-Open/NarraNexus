---
code_file: plugins/builtin.browser/src/narranexus_plugins/browser_module/_browser_module_impl/evidence.py
last_verified: 2026-09-22
stub: false
---

# Screenshot evidence

Successful policy-gated captures become ordinary image artifacts. The helper
resolves the agent's existing workspace, validates screenshot encoding/type
and artifact size limits, writes a unique image directory and calls
ArtifactService.register with the trusted user and event attribution.

It creates no screenshot table, alternate store or frontend renderer. Images
remain private agent artifacts; no team is inferred from page contents or
model arguments. A registration failure removes the newly written image so
sensitive unregistered evidence is not left behind. Tool responses carry the
artifact reference rather than base64 image data.
