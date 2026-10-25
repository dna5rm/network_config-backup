#!/usr/bin/env python3
"""PAN-OS redaction and version parsing for network_config-backup.

Filters (spec section 8: each platform file owns its own rule list):

    {{ output | panos_config_text }}  the configuration lines from a
                                      panos_op response dump
    {{ config | panos_redact }}   replace whole secret lines with a comment
    {{ redacted | panos_leaks }}  list the rule ids that still match a redacted
                                  text (the fail-closed backstop)
    {{ output | panos_version }}  the version token from 'show system info',
                                  empty string when nothing matches

Input is 'show config running' set-format output: the play asks the device
for set-format output first ('set cli config-output-format set') and
panos_config_text turns the single-line JSON dump panos_op returns into real
'set' command lines. Redaction rules drop the phash lines, the IKE key lines
and the secret/passcode/password assignments; leak rules catch a phash token
and any standalone 40 hex character key body wherever redaction missed them.
A matching line is replaced in full with panos_PLACEHOLDER. No rule prints
matched line content.

Redaction never trusts its input to carry physical lines. A whole op answer
can reach the filter as one long line, and such a line names every secret the
configuration holds, including a phash; matching it whole once deleted the
entire archive entry. Text without line breaks is therefore broken at the
'set' command boundaries before the rules run, so one match removes only the
command carrying the secret and every harmless command survives.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

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

# Every command of set-format PAN-OS output begins with 'set'.
_SET_BOUNDARY = re.compile(r"(?=set\s)")


def _physical_lines(config: str) -> List[str]:
    """The lines redaction works on.

    Multi-line input is used exactly as collected. A single physical line
    carries flattened set commands, so it is broken at the command
    boundaries: a secret match may then only remove its own command, never
    the whole configuration.
    """
    lines: List[str] = config.splitlines(keepends=True)
    if len(lines) > 1:
        return lines
    if not lines:
        return []
    return [part for part in _SET_BOUNDARY.split(lines[0]) if part]


def _line_matches(rules: List[Tuple[str, re.Pattern]], line: str) -> bool:
    return any(pattern.search(line) for _, pattern in rules)


def panos_config_text(output: Optional[str]) -> str:
    """Return the configuration text from a panos_op response dump.

    panos_op serialises the whole op response into one physical line: its
    stdout is the response XML parsed to a dict and json.dumps'ed. The
    running configuration sits in response.result - the 'set ...' commands
    as one text block when the device answered in set format, otherwise the
    nested configuration structure. Both must reach redaction as physical
    lines: a text result is returned with its own newlines, a nested result
    is rendered as YAML, one configuration element per line. Anything that
    is not a JSON dump of a response is passed through unchanged.
    """
    if output is None:
        return ""
    text = str(output).strip()
    if not text:
        return ""
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        return text
    if not isinstance(parsed, dict):
        return text
    result: Any = parsed.get("response", {}).get("result", "")
    if isinstance(result, str):
        return result
    if result is None:
        return ""
    return _flatten_result(result)


def _flatten_result(result: Any) -> str:
    """Render a nested configuration result one line per element."""
    try:
        import yaml
    except ImportError:
        return json.dumps(result, indent=2)
    try:
        return yaml.safe_dump(
            result, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception:
        return json.dumps(result, indent=2)


def panos_redact(config: Optional[str]) -> str:
    """Replace every sensitive line with PLACEHOLDER, preserving the rest."""
    if config is None:
        return ""
    if not isinstance(config, str):
        config = str(config)
    rebuilt: List[str] = []
    for line in _physical_lines(config):
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
            "panos_config_text": panos_config_text,
            "panos_redact": panos_redact,
            "panos_leaks": panos_leaks,
            "panos_version": panos_version,
        }