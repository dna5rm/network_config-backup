#!/usr/bin/env python3
"""PAN-OS redaction and version parsing for network_config-backup.

Filters (spec section 8: each platform file owns its own rule list):

    {{ config | panos_redact }}   replace whole secret lines with a comment
    {{ redacted | panos_leaks }}  list the rule ids that still match a redacted
                                  text (the fail-closed backstop)
    {{ output | panos_version }}  the version token from 'show system info',
                                  empty string when nothing matches

Input is 'show config running' set-format output. Redaction rules drop the
phash lines, the IKE key lines and the secret/passcode/password assignments;
leak rules catch a phash token and any standalone 40 hex character key body
wherever redaction missed them. A matching line is replaced in full with
panos_PLACEHOLDER. No rule prints matched line content.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

PLACEHOLDER = "# <sensitive line removed>"

REDACT_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "phash line",
        re.compile(r"\bphash\b", re.IGNORECASE),
    ),
    (
        "secret, passcode or password assignment",
        re.compile(r"\b(secret|passcode|password|pre-shared-key|psk|api-key)\s+[\"']?\S", re.IGNORECASE),
    ),
    (
        "IKE key line",
        re.compile(r"\bike\b[^\n]*\bkey\b", re.IGNORECASE),
    ),
]

LEAK_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "phash token",
        re.compile(r"\bphash\b", re.IGNORECASE),
    ),
    (
        "forty-hex key material",
        re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])"),
    ),
]

VERSION_PATTERNS = [
    re.compile(r"(?im)^\s*sw-version:\s*([0-9][^\s]+)"),
]


def _line_matches(rules: List[Tuple[str, re.Pattern]], line: str) -> bool:
    return any(pattern.search(line) for _, pattern in rules)


def panos_redact(config: Optional[str]) -> str:
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


def panos_leaks(config: Optional[str]) -> List[str]:
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


def panos_version(text: Optional[str]) -> str:
    """Return the version token from 'show system info', or ''."""
    if text is None:
        return ""
    for pattern in VERSION_PATTERNS:
        found = pattern.search(str(text))
        if found:
            return found.group(1)
    return ""


class FilterModule:
    """Expose the PAN-OS redaction and version filters."""

    def filters(self) -> Dict[str, object]:
        return {
            "panos_redact": panos_redact,
            "panos_leaks": panos_leaks,
            "panos_version": panos_version,
        }