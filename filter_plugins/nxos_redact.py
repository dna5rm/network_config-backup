#!/usr/bin/env python3
"""Cisco NX-OS redaction and version parsing for network_config-backup.

Filters (spec section 8: each platform file owns its own rule list):

    {{ config | nxos_redact }}   replace whole credential lines with a comment
    {{ redacted | nxos_leaks }}  list the rule ids that still match a redacted
                                 text (the fail-closed backstop)
    {{ output | nxos_version }}  the version token from 'show version' output,
                                 empty string when nothing matches

NX-OS prints the running version as 'system:      version 9.3(10)'; the
parser prefers that line and falls back to the NX-OS banner. A matching
redaction line is replaced in full with nxos_PLACEHOLDER. No rule prints
matched line content: the failure message names the rule id, not the secret.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

PLACEHOLDER = "! <sensitive line removed>"

REDACT_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "enable secret or enable password",
        re.compile(r"^\s*enable\s+(secret|password)\b", re.IGNORECASE),
    ),
    (
        "username line carrying a password",
        re.compile(r"^\s*username\s+\S+.*\b(password|secret)\b", re.IGNORECASE),
    ),
    (
        "password line",
        re.compile(r"^\s*password(\s+[0-7])?\s+\S+", re.IGNORECASE),
    ),
    (
        "snmp-server community or user",
        re.compile(r"^\s*snmp-server\s+(community|user)\b", re.IGNORECASE),
    ),
    (
        "tacacs-server or radius-server key",
        re.compile(r"^\s*(tacacs-server|radius-server)\s+key\b", re.IGNORECASE),
    ),
    (
        "crypto isakmp key",
        re.compile(r"crypto\s+isakmp\s+key", re.IGNORECASE),
    ),
    (
        "key material token",
        re.compile(r"\b(pre-shared-key|key-string|authentication-key|key-hash)\b", re.IGNORECASE),
    ),
    (
        "message-digest-key",
        re.compile(r"\bmessage-digest-key\b", re.IGNORECASE),
    ),
    (
        "type-7 key line",
        re.compile(r"^\s*key(\s+[0-7])?\s+\S+", re.IGNORECASE),
    ),
    (
        "key line carrying a value",
        re.compile(r"^\s*key\s+(?!chain\b|config-key\b|generate\b|pubkey-chain\b)\S+", re.IGNORECASE),
    ),
    (
        "loose secret or password assignment",
        re.compile(r"\b(secret|password)\s+([0-9]\s+)?\S+", re.IGNORECASE),
    ),
]

LEAK_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "salted password hash token",
        re.compile(r"\$[1-9]\$\$?[A-Za-z0-9/.-]{2,}\$[A-Za-z0-9/.-]{8,}"),
    ),
    (
        "type-7 key material",
        re.compile(r"\b[0-9A-F]{10,}\b"),
    ),
]

VERSION_PATTERNS = [
    re.compile(r"(?im)^\s*system:\s*version\s+(\S+)"),
    re.compile(r"(?im)\bNX-OS\s+v(\S+)"),
    re.compile(r"(?im)\bversion\s+([0-9][0-9A-Za-z().-]*)"),
]


def _line_matches(rules: List[Tuple[str, re.Pattern]], line: str) -> bool:
    return any(pattern.search(line) for _, pattern in rules)


def nxos_redact(config: Optional[str]) -> str:
    """Replace every sensitive line with PLACEHOLDER, preserving the rest."""
    if config is None:
        return ""
    if not isinstance(config, str):
        config = str(config)
    rebuilt: List[str] = []
    for line in config.splitlines(keepends=True):
        newline = ""
        body = line
        for candidate in ("\r\n", "\n", "\r"):
            if body.endswith(candidate):
                newline = candidate
                body = body[: -len(candidate)]
                break
        rebuilt.append((PLACEHOLDER if _line_matches(REDACT_RULES, body) else body) + newline)
    return "".join(rebuilt)


def nxos_leaks(config: Optional[str]) -> List[str]:
    """Rule ids that still match somewhere in the (redacted) text."""
    if config is None:
        return []
    return sorted(
        {
            label
            for line in str(config).splitlines()
            for label, pattern in LEAK_RULES
            if pattern.search(line)
        }
    )


def nxos_version(text: Optional[str]) -> str:
    """Return the version token from 'show version' output, or ''."""
    if text is None:
        return ""
    for pattern in VERSION_PATTERNS:
        found = pattern.search(str(text))
        if found:
            return found.group(1)
    return ""


class FilterModule:
    """Expose the NX-OS redaction and version filters."""

    def filters(self) -> Dict[str, object]:
        return {
            "nxos_redact": nxos_redact,
            "nxos_leaks": nxos_leaks,
            "nxos_version": nxos_version,
        }