#!/usr/bin/env python3
"""NetBox platform metadata for network_config-backup (read-only).

Returns one dict mapping the platform slug to that platform's
``ansible_network_os`` and ``ansible_user`` custom fields:

    GET {NETBOX_API}/api/dcim/platforms/?limit=0

NetBox inventory variables cannot carry platform custom fields, and device
``cf_`` filters do not reach platform fields, so the play reads the two
person-maintained fields here instead. Nothing is ever written.

Choice custom fields (a select on NetBox 4.x) arrive as {"value", "label"}
objects, not strings; the lookup unwraps them to the bare value so the
play's OS gate sees the string it expects.

NETBOX_API and NETBOX_TOKEN come from the environment. The result is memoized
per process, which keeps the number of HTTP calls low across many hosts.
"""

from __future__ import annotations

import json
import os

from ansible.errors import AnsibleLookupError
from ansible.module_utils.urls import open_url
from ansible.plugins.lookup import LookupBase

_MEMO: dict = {}


def unwrap_select(field: object) -> object:
    """Return a custom field's plain value.

    A NetBox 4.x select (choice) field serializes as a {"value", "label"}
    dict; a text field stays a plain string. Unwrap the dict's value and
    pass every other shape through unchanged.
    """
    if isinstance(field, dict):
        return field.get("value") or ""
    return field or ""


def read_platform_meta(api_endpoint: str, token: str) -> dict:
    """Return {platform-slug: {ansible_network_os, ansible_user}}."""
    url = api_endpoint.rstrip("/") + "/api/dcim/platforms/?limit=0"
    headers = {
        "Authorization": "Token %s" % token,
        "Accept": "application/json",
    }
    response = open_url(url, headers=headers, validate_certs=True)
    payload = json.loads(response.read())
    meta = {}
    for platform in payload.get("results", []):
        slug = platform.get("slug") or ""
        if not slug:
            continue
        custom_fields = platform.get("custom_fields") or {}
        meta[slug] = {
            "ansible_network_os": unwrap_select(custom_fields.get("ansible_network_os")),
            "ansible_user": unwrap_select(custom_fields.get("ansible_user")),
        }
    return meta


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        api_endpoint = (os.environ.get("NETBOX_API") or "").strip().rstrip("/")
        token = os.environ.get("NETBOX_TOKEN") or ""
        if not api_endpoint or not token:
            raise AnsibleLookupError(
                "config_backup_platforms: NETBOX_API and NETBOX_TOKEN must "
                "be set in the environment."
            )
        if api_endpoint not in _MEMO:
            _MEMO[api_endpoint] = read_platform_meta(api_endpoint, token)
        return [_MEMO[api_endpoint]]