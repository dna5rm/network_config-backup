"""Static no-task guarantee tests (spec 12.5, 4, 8).

Spec 12.5: the task files of f5.bigip, cisco.meraki and velocloud must not
call a connection plugin. They parse as YAML here and may only include the
placeholder task file and end the host.
Spec 4: those three placeholders must set validation_state to no_task.
Spec 8: every platform rule list is non-empty (asserted in test_redaction).

The NetBox writeback tasks run on the runner and are allowed; a device
connection module is not.
"""

import unittest
from pathlib import Path

import yaml

from tests import _REPO

TASKS = _REPO / "tasks"

# Module bases (name after the last dot) allowed in the placeholder files.
ALLOWED_BASES = {"include_tasks", "meta", "vars", "when", "name"}
FORBIDDEN_BASES = {
    "command", "raw", "expect", "script", "shell", "telnet", "uri",
}
FORBIDDEN_FULL_PREFIXES = (
    "cisco.", "junipernetworks.", "paloaltonetworks.", "f5networks.",
    "ansible.netcommon.", "ansible.netcli.", "vyos.",
)


def _load(name):
    with open(TASKS / name, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _base(key: str) -> str:
    return str(key).rsplit(".", 1)[-1]


def _module_key(task: dict, base: str):
    for key, value in task.items():
        if _base(key) == base:
            return key, value
    return None, None


def _walk_tasks(node):
    """Yield every task dict, recursing into block/rescue/always and loops."""
    if isinstance(node, dict):
        for base in ("block", "rescue", "always"):
            if base in node:
                for inner in _walk_tasks(node[base]):
                    yield inner
        stripped = {k: v for k, v in node.items() if k not in ("block", "rescue", "always")}
        if stripped and any(_base(k) not in ("name",) for k in stripped):
            yield node
        elif "block" not in node and "rescue" not in node and "always" not in node:
            yield node
    elif isinstance(node, list):
        for item in node:
            yield from _walk_tasks(item)


def _all_tasks(name):
    return list(_walk_tasks(_load(name)))


class Spec12_5PlaceholderFilesDoNotConnect(unittest.TestCase):
    def _placeholder_file(self, name):
        doc = _load(name)
        self.assertIsInstance(doc, list)
        for task in doc:
            bases = {_base(k) for k in task}
            self.assertTrue(bases <= {"include_tasks", "meta", "name", "vars", "when"},
                            "%s has unexpected task keys %s" % (name, bases))
        include_tasks = [t for t in doc if _module_key(t, "include_tasks")[0]]
        meta_tasks = [t for t in doc if _module_key(t, "meta")[0]]
        self.assertEqual(len(include_tasks), 1)
        self.assertEqual(len(meta_tasks), 1)
        self.assertEqual(include_tasks[0]["ansible.builtin.include_tasks"]["file"],
                         "{{ playbook_dir }}/tasks/_no_task.yml")
        self.assertEqual(meta_tasks[0]["ansible.builtin.meta"], "end_host")

    def test_f5_file_shape(self):
        self._placeholder_file("f5.yml")

    def test_meraki_file_shape(self):
        self._placeholder_file("meraki.yml")

    def test_velocloud_file_shape(self):
        self._placeholder_file("velocloud.yml")

    def test_no_task_file_records_no_task(self):
        doc = _load("_no_task.yml")
        self.assertTrue(all(_module_key(t, "include_tasks")[0] for t in doc))
        for task in doc:
            key, value = _module_key(task, "include_tasks")
            self.assertEqual(value["file"], "{{ playbook_dir }}/tasks/_netbox_patch.yml")
            self.assertEqual(task["vars"]["config_backup_patch_fields"],
                             {"validation_state": "no_task"})

    def test_no_task_files_have_no_connection_modules(self):
        for name in ("f5.yml", "meraki.yml", "velocloud.yml"):
            for task in _all_tasks(name):
                for key in task:
                    base = _base(key)
                    self.assertNotIn(base, FORBIDDEN_BASES,
                                     "%s contains forbidden module %s" % (name, key))
                    self.assertFalse(key.startswith(FORBIDDEN_FULL_PREFIXES),
                                     "%s contains forbidden module %s" % (name, key))

    def test_panos_task_file_uses_panos_op_modules(self):
        doc = _all_tasks("panos.yml")
        op_tasks = [t for t in doc if any(str(k).endswith("panos_op") for k in t)]
        # The collection order is a contract: set-format output first, then
        # the two reads, then the default-format restore on each exit path
        # (block rescue and the success path after the block). A bare
        # 'show config running' answers as one flattened line that the
        # redactor cannot split, so the set-format request is mandatory.
        commands = [_module_key(t, "panos_op")[1]["cmd"] for t in op_tasks]
        self.assertEqual(
            commands,
            ["set cli config-output-format set",
             "show config running",
             "show system info",
             "set cli config-output-format default",
             "set cli config-output-format default"])
        for task in op_tasks:
            _, module = _module_key(task, "panos_op")
            self.assertIn("cmd", module)
            provider = module["provider"]
            self.assertEqual(set(provider.keys()), {"ip_address", "username", "password"})
            self.assertNotIn("api_key", module)
            self.assertTrue(task["no_log"])


class NetboxPatchNeverTouchesADevice(unittest.TestCase):
    def test_writeback_is_delegated_and_no_log(self):
        doc = _all_tasks("_netbox_patch.yml")
        uri_tasks = [t for t in doc if _module_key(t, "uri")[0]]
        self.assertEqual(len(uri_tasks), 2)
        for task in uri_tasks:
            self.assertEqual(task["delegate_to"], "localhost")
            self.assertTrue(task["no_log"])
        fail_tasks = [t for t in doc if _module_key(t, "fail")[0]]
        self.assertEqual(len(fail_tasks), 3)
        patch_task = [t for t in uri_tasks if _module_key(t, "uri")[1]["method"] == "PATCH"][0]
        body = patch_task["ansible.builtin.uri"]["body"]
        self.assertEqual(set(body.keys()), {"custom_fields"})

    def test_archive_write_is_delegated(self):
        doc = _all_tasks("_archive_write.yml")
        for task in doc:
            if _module_key(task, "copy")[0] or _module_key(task, "file")[0]:
                self.assertEqual(task["delegate_to"], "localhost")


class DispatchCoversTheClosedSet(unittest.TestCase):
    def _group_vars(self):
        return yaml.safe_load((Path(_REPO) / "group_vars" / "all.yml").read_text())

    def test_task_files_mapping_matches_spec_choices(self):
        mapping = self._group_vars()["config_backup_task_files"]
        expected = {
            "cisco.ios.ios": "ios.yml",
            "cisco.nxos.nxos": "nxos.yml",
            "junipernetworks.junos.junos": "junos.yml",
            "paloaltonetworks.panos.panos": "panos.yml",
            "f5.bigip": "f5.yml",
            "cisco.meraki": "meraki.yml",
            "velocloud": "velocloud.yml",
        }
        self.assertEqual(mapping, expected)
        for name in expected.values():
            self.assertTrue((TASKS / name).exists())

    def test_os_values_are_the_closed_set(self):
        group_vars = self._group_vars()
        self.assertEqual(group_vars["config_backup_os_values"], [
            "cisco.ios.ios", "cisco.nxos.nxos", "junipernetworks.junos.junos",
            "paloaltonetworks.panos.panos", "f5.bigip", "cisco.meraki",
            "velocloud",
        ])
        self.assertEqual(group_vars["config_backup_connection_values"],
                         ["network_cli", "httpapi", "netconf", "local"])


class PanosUsesTheRunnerConnector(unittest.TestCase):
    def test_panos_defaults_to_local_connection(self):
        group_vars = yaml.safe_load((Path(_REPO) / "group_vars" / "all.yml").read_text())
        self.assertEqual(group_vars["config_backup_connection_defaults"]["paloaltonetworks.panos.panos"], "local")
        self.assertEqual(group_vars["config_backup_connection_defaults"]["f5.bigip"], "local")
        self.assertEqual(group_vars["config_backup_connection_defaults"]["cisco.meraki"], "local")
        self.assertEqual(group_vars["config_backup_connection_defaults"]["velocloud"], "local")


if __name__ == "__main__":
    unittest.main()