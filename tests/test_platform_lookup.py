"""Platform metadata lookup tests (spec 4).

The play reads each platform's ``ansible_network_os`` and ``ansible_user``
custom fields through this lookup. On NetBox 4.x a select field serializes
as a {"value", "label"} object, so the lookup must unwrap the dict's value;
text fields and plain strings pass through unchanged. NetBox is faked at
the ``open_url`` boundary, so the suite runs without a device or a token.
"""

import importlib.util
import json
import unittest

from tests import _REPO

_FIXTURE_API = "http://192.0.2.10:8000"
_FIXTURE_TOKEN = "fixture-only"


def _lookup_module():
    path = _REPO / "lookup_plugins" / "config_backup_platforms.py"
    spec = importlib.util.spec_from_file_location("config_backup_platforms", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_platform_meta(results: list) -> dict:
    module = _lookup_module()
    payload = json.dumps({"results": results}).encode("utf-8")

    class _Response:
        def read(self) -> bytes:
            return payload

    def fake_open_url(url, headers=None, validate_certs=True):
        return _Response()

    module.open_url = fake_open_url
    return module.read_platform_meta(_FIXTURE_API, _FIXTURE_TOKEN)


class UnwrapSelect(unittest.TestCase):
    def test_select_dict_unwraps_to_value(self):
        module = _lookup_module()
        self.assertEqual(
            module.unwrap_select({"value": "example.os", "label": "Example OS"}),
            "example.os")

    def test_plain_string_stays_a_string(self):
        module = _lookup_module()
        self.assertEqual(module.unwrap_select("example.os"), "example.os")

    def test_none_composes_empty(self):
        module = _lookup_module()
        self.assertEqual(module.unwrap_select(None), "")

    def test_valueless_dict_composes_empty(self):
        module = _lookup_module()
        self.assertEqual(module.unwrap_select({"label": "Example OS"}), "")


class ReadPlatformMeta(unittest.TestCase):
    def test_select_fields_unwrap_to_values(self):
        meta = _read_platform_meta([{
            "slug": "example-os",
            "custom_fields": {
                "ansible_network_os": {"value": "example.os", "label": "Example OS"},
                "ansible_user": "user-a",
            },
        }])
        self.assertEqual(
            meta,
            {"example-os": {"ansible_network_os": "example.os",
                            "ansible_user": "user-a"}})

    def test_plain_text_fields_stay_strings(self):
        meta = _read_platform_meta([{
            "slug": "example-os",
            "custom_fields": {
                "ansible_network_os": "example.os",
                "ansible_user": "user-a",
            },
        }])
        self.assertEqual(
            meta,
            {"example-os": {"ansible_network_os": "example.os",
                            "ansible_user": "user-a"}})

    def test_missing_custom_fields_default_to_empty(self):
        meta = _read_platform_meta([{"slug": "example-os", "custom_fields": {}}])
        self.assertEqual(
            meta,
            {"example-os": {"ansible_network_os": "", "ansible_user": ""}})

    def test_platform_without_slug_is_skipped(self):
        meta = _read_platform_meta(
            [{"slug": "", "custom_fields": {"ansible_network_os": "example.os"}}])
        self.assertEqual(meta, {})


if __name__ == "__main__":
    unittest.main()