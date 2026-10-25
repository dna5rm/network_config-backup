#!/usr/bin/env python3
"""HTTP status extraction for registered ansible.builtin.uri results.

Different ansible-core versions register the HTTP status of a uri task
under different keys (``status`` on current releases, ``status_code`` on
older ones). The plays need the status to decide fail-closed behaviour and
to print a sanitized failure line, so this filter reads whichever key the
running core provides and returns 0 when neither is present.
"""

from __future__ import annotations

from typing import Dict

_URI_STATUS_KEYS = ("status", "status_code")


def config_backup_http_status(result: object) -> int:
    """Return the HTTP status of a registered uri result, or 0."""
    if not isinstance(result, Dict):
        return 0
    for key in _URI_STATUS_KEYS:
        value = result.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


class FilterModule:
    """Expose the uri status filter."""

    def filters(self) -> Dict[str, object]:
        return {"config_backup_http_status": config_backup_http_status}