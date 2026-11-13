#!/usr/bin/env python3
"""Junos redaction for network_config-backup.

Filters (spec section 8: each platform file owns its own rule list):

    {{ config | junos_redact }}   replace whole credential lines with a comment
    {{ redacted | junos_leaks }}  list the rule ids that still match a redacted
                                  text (the fail-closed backstop)

Input is 'show configuration | display set' output: one 'set ...' line per
statement. Redaction rules drop set lines that assign a password, secret,
pre-shared key, SNMP community or authentication key; leak rules catch the
Junos encrypted password token shapes (for example $9$... or $1$salt$hash)
wherever redaction missed them. A matching line is replaced in full with
junos_PLACEHOLDER. No rule prints matched line content.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

PLACEHOLDER = "# <sensitive line removed>"

REDACT_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "set line assigning a password or secret",
        re.compile(r"\bset\s+\S+[^\n]*\b(password|secret|psk|passphrase)\s+[\"']?\S", re.IGNORECASE),
    ),
    (
        "encrypted or plain-text password",
        re.compile(r"\b(encrypted-password|plain-text-password|plaintext-password)\b", re.IGNORECASE),
    ),
    (
        "pre-shared-key",
        re.compile(r"\b(pre-shared-key|psk)\b", re.IGNORECASE),
    ),
    (
        "snmp community assignment",
        re.compile(r"\bset\s+snmp\s+community\b", re.IGNORECASE),
    ),
    (
        "authentication key",
        re.compile(r"\bauthentication-key\b", re.IGNORECASE),
    ),
]

LEAK_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "Junos encrypted password token",
        re.compile(r"\$[0-9A-Za-z]{1,6}\$[A-Za-z0-9+/._-]{8,}"),
    ),
]


def _line_matches(rules: List[Tuple[str, re.Pattern]], line: str) -> bool:
    return any(pattern.search(line) for _, pattern in rules)


def junos_redact(config: Optional[str]) -> str:
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


def junos_leaks(config: Optional[str]) -> List[str]:
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


class FilterModule:
    """Expose the Junos redaction filters."""

    def filters(self) -> Dict[str, object]:
        return {
            "junos_redact": junos_redact,
            "junos_leaks": junos_leaks,
        }
