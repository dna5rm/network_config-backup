"""Inventory render tests (spec 12.1 and 12.2).

The render helper copies inventory/netbox.yml.example and inserts the
optional site and platform runner filters. Without filters no site key may
appear anywhere in the rendered inventory; with NETBOX_SITE=site-a that one
filter must appear and no other site.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import jinja2
import yaml

from tests import _REPO

RENDER = _REPO / "scripts" / "render_inventory.sh"
EXAMPLE = _REPO / "inventory" / "netbox.yml.example"


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


class Spec12_1NoSiteKeyWithoutFilter(unittest.TestCase):
    def test_no_site_filter_in_render(self):
        rc, doc = _render({})
        self.assertEqual(rc, 0)
        self.assertEqual(_walk_keys(doc, "site"), [])
        self.assertEqual(_walk_keys(doc, "platform"), [])
        self.assertEqual(doc.get("device_query_filters"),
                         [{"status": "active"}, {"has_primary_ip": "true"}])


class Spec12_2SiteFilterPresent(unittest.TestCase):
    def test_site_filter_only_site_a(self):
        rc, doc = _render({"NETBOX_SITE": "site-a"})
        self.assertEqual(rc, 0)
        self.assertEqual(doc.get("device_query_filters"),
                         [{"status": "active"}, {"has_primary_ip": "true"},
                          {"site": "site-a"}])
        self.assertEqual([v for v in _walk_keys(doc, "site") if isinstance(v, str)],
                         ["site-a"])

    def test_platform_filter_only_platform_a(self):
        rc, doc = _render({"NETBOX_PLATFORM": "platform-a"})
        self.assertEqual(rc, 0)
        self.assertEqual(doc.get("device_query_filters"),
                         [{"status": "active"}, {"has_primary_ip": "true"},
                          {"platform": "platform-a"}])

    def test_both_filters(self):
        rc, doc = _render({"NETBOX_SITE": "site-a", "NETBOX_PLATFORM": "platform-a"})
        self.assertEqual(rc, 0)
        self.assertEqual(doc.get("device_query_filters"),
                         [{"status": "active"}, {"has_primary_ip": "true"},
                          {"site": "site-a"}, {"platform": "platform-a"}])


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
        self.assertEqual(doc.get("plugin"), "netbox.netbox.nb_inventory")
        self.assertEqual(doc.get("group_by"), ["platforms"])
        self.assertTrue(doc.get("group_names_raw"))
        self.assertEqual(doc.get("vm_query_filters"),
                         [{"name": "config-backup-no-virtual-machines"}])
        self.assertNotIn("api_endpoint", doc)
        self.assertNotIn("token", doc)
        compose = doc.get("compose") or {}
        self.assertIn("ansible_host", compose)
        self.assertIn("nb_site_slug", compose)
        self.assertIn("nb_platform_slug", compose)
        self.assertIn("nb_device_id", compose)


class ComposePlatformSlugAcceptsBothShapes(unittest.TestCase):
    """The nb_platform_slug compose must work for either platform shape.

    With group_by platforms and group_names_raw, the netbox plugin sets the
    host platform variable to the slug string before compose runs, so a
    bare platform.slug composes empty; the expression falls back to the
    string itself, and a real platform object still wins.
    """

    def _compose_slug(self, variables: dict) -> str:
        rc, doc = _render({})
        self.assertEqual(rc, 0)
        return jinja2.Template(doc["compose"]["nb_platform_slug"]).render(**variables)

    def test_platform_object_uses_its_slug(self):
        self.assertEqual(
            self._compose_slug({"platform": {"slug": "example-os", "name": "Example OS"}}),
            "example-os")

    def test_platform_string_is_used_directly(self):
        self.assertEqual(self._compose_slug({"platform": "example-os"}), "example-os")

    def test_missing_platform_composes_empty(self):
        self.assertEqual(self._compose_slug({}), "")


if __name__ == "__main__":
    unittest.main()