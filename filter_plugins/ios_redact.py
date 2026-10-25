#!/usr/bin/env python3
"""Cisco IOS / IOS-XE redaction and version parsing for network_config-backup.

Filters (spec section 8: each platform file owns its own rule list):

    {{ config | ios_redact }}   replace whole credential lines with a comment
    {{ redacted | ios_leaks }}  list the rule ids that still match a redacted
                                text (the fail-closed backstop)
    {{ output | ios_version }}  the version token from 'show version' output,
                                empty string when nothing matches

A matching redaction line is replaced in full, indentation included, with
ios_PLACEHOLDER. Other lines and the trailing newline survive. Leak rules
catch secret shapes redaction might have missed; they are checked against
the redacted text before anything is written. No rule prints matched line
content: the failure message names the rule id, not the secret.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

PLACEHOLDER = "! <sensitive line removed>"

# (rule id, compiled pattern). Per line, case-insensitive; any match removes
# the whole line. Location and contact lines under snmp-server stay, as do
# plain key chain names and 'crypto key generate'.
REDACT_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "enable secret or enable password",
        re.compile(r"^\s*enable\s+(secret|password)\b", re.IGNORECASE),
    ),
    (
        "username line carrying a secret or password",
        re.compile(r"^\s*username\s+\S+.*\b(secret|password)\b", re.IGNORECASE),
    ),
    (
        "password line",
        re.compile(r"^\s*password(\s+[0-7])?\s+\S+", re.IGNORECASE),
    ),
    (
        "snmp-server community, user or host",
        re.compile(r"^\s*snmp-server\s+(community|user|host)\b", re.IGNORECASE),
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
        "wpa-psk",
        re.compile(r"\bwpa-psk\b", re.IGNORECASE),
    ),
    (
        "message-digest-key",
        re.compile(r"\bmessage-digest-key\b", re.IGNORECASE),
    ),
    (
        "type-7 key line",
        re.compile(r"^\s*key(\s+[0-7])?\s+\S+", re.IGNORECASE),
    ),
    # The leak backstop flags type-7 key material as a token shape, not as
    # a command word, so redaction must be at least that wide: any line
    # carrying a type-7 token anywhere is replaced whole. This rule is
    # deliberately wider than the (case-sensitive) leak rule, which only
    # sees uppercase hex, so a line that leaks can no longer survive
    # redaction.
    (
        "type-7 key material token",
        re.compile(r"\b[0-9A-F]{10,}\b", re.IGNORECASE),
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

# A redacted text must not contain these shapes anywhere. A leftover match
# fails the host and no file is written.
LEAK_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "salted password hash token",
        re.compile(r"\$[1-9]\$[A-Za-z0-9/.-]{2,}\$[A-Za-z0-9/.-]{8,}"),
    ),
    (
        "type-7 key material",
        re.compile(r"\b[0-9A-F]{10,}\b"),
    ),
]

# show version: 'Version' following a line start, comma or dot.
VERSION_PATTERNS = [
    re.compile(r"(?im)(?:^|[,.\s])Version\s+([0-9A-Za-z.()]+)"),
]


def _line_matches(rules: List[Tuple[str, re.Pattern]], line: str) -> bool:
    return any(pattern.search(line) for _, pattern in rules)


def ios_redact(config: Optional[str]) -> str:
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


def ios_leaks(config: Optional[str]) -> List[str]:
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


def ios_version(text: Optional[str]) -> str:
    """Return the version token from 'show version' output, or ''."""
    if text is None:
        return ""
    for pattern in VERSION_PATTERNS:
        found = pattern.search(str(text))
        if found:
            return found.group(1)
    return ""


class FilterModule:
    """Expose the IOS redaction and version filters."""

    def filters(self) -> Dict[str, object]:
        return {
            "ios_redact": ios_redact,
            "ios_leaks": ios_leaks,
            "ios_version": ios_version,
        }