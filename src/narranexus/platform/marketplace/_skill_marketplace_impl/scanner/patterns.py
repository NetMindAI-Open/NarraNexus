"""
@file_name: patterns.py
@author: NetMind.AI
@date: 2026-07-20
@description: Rule tables for the skill security scanner.

HIGH rules reject a skill outright; LOW rules warn (skill stays installable,
issues are shown to the user / recorded in skill_scan_results). Text rules
run on every scannable file; AST rules run on parsed Python only.
"""

import re
from dataclasses import dataclass

SCANNER_VERSION = "1.0.0"

SEVERITY_HIGH = "high"
SEVERITY_LOW = "low"


@dataclass(frozen=True)
class TextRule:
    rule: str
    severity: str
    pattern: re.Pattern
    description: str


# --- Text rules (regex, any scannable file: .py/.sh/.md/...) ----------------

TEXT_RULES: list[TextRule] = [
    TextRule(
        rule="shell_pipe_exec",
        severity=SEVERITY_HIGH,
        pattern=re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba|z|da)?sh\b"),
        description="Downloads and pipes remote content into a shell",
    ),
    TextRule(
        rule="sensitive_path",
        severity=SEVERITY_HIGH,
        # NOTE: "credentials" only matches in path-like form (.aws/credentials,
        # .git-credentials, some/dir/credentials) — the bare English word in
        # skill docs must NOT reject the skill.
        pattern=re.compile(
            r"~/\.ssh|/etc/passwd|/etc/shadow|~/\.aws"
            r"|(?:^|[\s'\"(=/])\.env\b"
            r"|(?:\.aws/|\.git-?|/)credentials\b"
        ),
        description="References credential stores or sensitive system paths",
    ),
]


# --- AST rules (Python call sites) ------------------------------------------
# Maps a fully dotted callable name to (rule, description). Module-level
# prefixes ending in "." match any attribute of that module (subprocess.*).

AST_CALL_RULES: dict[str, tuple[str, str]] = {
    "eval": ("eval_exec", "Dynamic code evaluation"),
    "exec": ("eval_exec", "Dynamic code execution"),
    "compile": ("compile_call", "Compiles source at runtime"),
    "__import__": ("dynamic_import", "Dynamic import"),
    "importlib.import_module": ("dynamic_import", "Dynamic import"),
    "os.system": ("subprocess_exec", "Spawns a shell command"),
    "os.popen": ("subprocess_exec", "Spawns a shell command"),
    "subprocess.": ("subprocess_exec", "Spawns a subprocess"),
    "requests.post": ("network_post", "Outbound HTTP POST"),
    "requests.put": ("network_post", "Outbound HTTP PUT"),
    "httpx.post": ("network_post", "Outbound HTTP POST"),
    "socket.": ("socket_usage", "Raw socket usage"),
    "os.walk": ("fs_walk", "Filesystem traversal"),
    "pickle.load": ("pickle_load", "Unpickling untrusted data"),
    "pickle.loads": ("pickle_load", "Unpickling untrusted data"),
    "base64.b64decode": ("base64_decode", "Decodes embedded base64 payload"),
    "os.symlink": ("symlink", "Creates symlinks (sandbox-escape vector)"),
}

# glob.glob is only suspicious with a recursive ** pattern; handled specially
# in static.py so plain `glob.glob("*.txt")` stays clean.
GLOB_RULE = ("fs_walk", "Recursive filesystem glob")

UNPARSABLE_RULE = (
    "unparsable_python",
    "Python file could not be parsed — AST rules did not run on it",
)

# File extensions we scan as text; everything else is checked for binary
# content and skipped if it has NUL bytes.
SCANNABLE_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini"}

MAX_SCAN_FILE_BYTES = 1_000_000


# --- Dependency advisories (MVP: static in-repo list) -----------------------
# package -> list of (vulnerable_specifier, advisory). Specifier supports the
# forms we can check without a resolver: "<X.Y.Z" and "==X.Y.Z".

KNOWN_VULNERABLE: dict[str, list[tuple[str, str]]] = {
    "requests": [("<2.20.0", "CVE-2018-18074: Authorization header leak on redirect")],
    "pyyaml": [("<5.4", "CVE-2020-14343: arbitrary code execution via full_load")],
    "urllib3": [("<1.26.5", "CVE-2021-33503: catastrophic backtracking in URL parsing")],
    "pillow": [("<9.0.1", "CVE-2022-24303: path traversal in tempfile handling")],
}
