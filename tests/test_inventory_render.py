"""Inventory render and plugin helper tests.

The render helper copies inventory/netbox.yml.example and inserts the
optional site and platform runner filters. Without filters no site key may
appear anywhere in the rendered inventory; with NETBOX_SITE=site-a that one
filter must appear and no other site.

The plugin helper tests run against inventory_plugins/config_backup_netbox
without ansible installed and without a live NetBox.
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import _REPO

import yaml

RENDER = _REPO / "scripts" / "render_inventory.sh"
EXAMPLE = _REPO / "inventory" / "netbox.yml.example"
PLUGIN = _REPO / "inventory_plugins" / "config_backup_netbox.py"

BASE_FILTERS = [{"status": "active"}, {"has_primary_ip": "true"}]


def _render(env_overrides: dict) -> tuple[int, dict]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("NETBOX_SITE", "NETBOX_PLATFORM")}
    env.update(env_overrides)
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as dst:
        proc = subprocess.run(
            ["bash", str(RENDER), str(EXAMPLE), dst.name],
            capture_output=True, text=True, env=env,
        )
    try:
        if proc.returncode != 0:
            return proc.returncode, {}
        with open(dst.name, encoding="utf-8") as handle:
            return 0, yaml.safe_load(handle)
    finally:
        Path(dst.name).unlink(missing_ok=True)


def _walk_keys(node, wanted: str) -> list:
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == wanted:
                hits.append(value)
            hits.extend(_walk_keys(value, wanted))
    elif isinstance(node, list):
        for item in node:
            hits.extend(_walk_keys(item, wanted))
    return hits


def _plugin_module():
    spec = importlib.util.spec_from_file_location("config_backup_netbox", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Spec12_1NoSiteKeyWithoutFilter(unittest.TestCase):
    def test_no_site_filter_in_render(self):
        rc, doc = _render({})
        self.assertEqual(rc, 0)
        self.assertEqual(_walk_keys(doc, "site"), [])
        self.assertEqual(doc.get("device_query_filters"), BASE_FILTERS)


class Spec12_2SiteFilterPresent(unittest.TestCase):
    def test_site_filter_only_site_a(self):
        rc, doc = _render({"NETBOX_SITE": "site-a"})
        self.assertEqual(rc, 0)
        self.assertEqual(doc.get("device_query_filters"),
                         BASE_FILTERS + [{"site": "site-a"}])
        self.assertEqual([v for v in _walk_keys(doc, "site") if isinstance(v, str)],
                         ["site-a"])

    def test_platform_filter_only_platform_a(self):
        rc, doc = _render({"NETBOX_PLATFORM": "platform-a"})
        self.assertEqual(rc, 0)
        self.assertEqual(doc.get("device_query_filters"),
                         BASE_FILTERS + [{"platform": "platform-a"}])

    def test_both_filters(self):
        rc, doc = _render({"NETBOX_SITE": "site-a", "NETBOX_PLATFORM": "platform-a"})
        self.assertEqual(rc, 0)
        self.assertEqual(doc.get("device_query_filters"),
                         BASE_FILTERS + [{"site": "site-a"}, {"platform": "platform-a"}])


class RenderRefusesNonSlugs(unittest.TestCase):
    def test_site_not_a_slug_is_refused(self):
        rc, doc = _render({"NETBOX_SITE": "bad slug!"})
        self.assertEqual(rc, 2)
        self.assertEqual(doc, {})

    def test_platform_not_a_slug_is_refused(self):
        rc, doc = _render({"NETBOX_PLATFORM": "../../etc"})
        self.assertEqual(rc, 2)
        self.assertEqual(doc, {})


class RenderedInventoryShape(unittest.TestCase):
    def test_membership_and_vm_shape_unchanged(self):
        rc, doc = _render({})
        self.assertEqual(rc, 0)
        self.assertEqual(doc.get("plugin"), "config_backup_netbox")
        self.assertTrue(doc.get("group_names_raw") is None)
        self.assertEqual(doc.get("vm_query_filters"),
                         [{"name": "config-backup-no-virtual-machines"}])
        self.assertNotIn("api_endpoint", doc)
        self.assertNotIn("token", doc)
        self.assertNotIn("has_platform",
                         str(doc.get("device_query_filters")))
        self.assertNotIn("nb_device_id", doc)

    def test_compose_and_group_by_are_gone(self):
        rc, doc = _render({})
        self.assertEqual(rc, 0)
        self.assertNotIn("compose", doc)
        self.assertNotIn("group_by", doc)


def _render_text() -> str:
    return EXAMPLE.read_text(encoding="utf-8")


class ConfigBackupNetboxPluginHelpers(unittest.TestCase):
    """platform_slug and query_pairs decide membership and the querystring.

    has_platform must not reach the NetBox query: Citco NetBox 4.4 does not
    advertise it, and the plugin removes devices with no platform itself.
    """

    def test_platform_object_uses_its_slug(self):
        module = _plugin_module()
        self.assertEqual(
            module.platform_slug({"platform": {"slug": "example-os",
                                               "name": "Example OS"}}),
            "example-os")

    def test_platform_string_is_used_directly(self):
        module = _plugin_module()
        self.assertEqual(module.platform_slug({"platform": "example-os"}), "example-os")

    def test_missing_or_null_platform_is_empty(self):
        module = _plugin_module()
        self.assertEqual(module.platform_slug({"platform": None}), "")
        self.assertEqual(module.platform_slug({}), "")

    def test_query_pairs_flatten_filters(self):
        module = _plugin_module()
        pairs = module.query_pairs([{"status": "active"},
                                    {"has_primary_ip": "true"}])
        self.assertEqual(pairs, [("status", "active"), ("has_primary_ip", "true")])

    def test_query_pairs_drop_has_platform(self):
        module = _plugin_module()
        pairs = module.query_pairs([{"status": "active"},
                                    {"has_platform": "true"},
                                    {"site": "site-a"}])
        self.assertEqual(pairs, [("status", "active"), ("site", "site-a")])

    def test_query_pairs_render_bools(self):
        module = _plugin_module()
        self.assertEqual(module.query_pairs([{"has_primary_ip": True}]),
                         [("has_primary_ip", "true")])

    def test_query_pairs_handle_junk(self):
        module = _plugin_module()
        self.assertEqual(module.query_pairs([None, {}, {"status": "active"}]),
                         [("status", "active")])
        self.assertEqual(module.query_pairs(None), [])


if __name__ == "__main__":
    unittest.main()
