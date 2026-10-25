"""Bootstrap field payload test.

Runs bootstrap_netbox.yml in real (non-check) mode against a mock NetBox
and asserts that both the create and the update request for the six
config-backup custom fields carry group_name="Backup".

Two runs:
  - every field already exists: the play must PATCH all six fields and
    each patch body must set group_name="Backup";
  - one select field is missing: the play must POST it with
    group_name="Backup" and its choice set resolved, while still
    PATCHing the five existing fields with group_name="Backup".

The mock answers from the field definitions in the play itself, so the
test cannot drift from the play's own choice sets. Skipped with a clear
reason when ansible-playbook is not installed.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import yaml

from tests import _REPO

SIX_FIELDS = {
    "ansible_network_os",
    "ansible_user",
    "observed_version",
    "observed_at",
    "validation_state",
    "desired_version",
}
# The create path is exercised with a select field, the harder kind: its
# create body must also carry the resolved choice set.
ABSENT_FIELD = "validation_state"

ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")

CHOICE_SET_PREFIX = "config_backup_"
CHOICE_SET_ID = 11


def _play_fields():
    doc = yaml.safe_load(
        (_REPO / "bootstrap_netbox.yml").read_text(encoding="utf-8"))
    return doc[0]["vars"]["config_backup_fields"]


class _MockNetBoxHandler(BaseHTTPRequestHandler):
    state = None  # injected by the test

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _path(self):
        return urlparse(self.path).path

    def _query(self):
        return dict(pair.split("=", 1)
                    for pair in urlparse(self.path).query.split("&")
                    if "=" in pair)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = self._path()
        state = _MockNetBoxHandler.state
        if path == "/api/extras/custom-field-choice-sets/":
            name = self._query()["name"]
            field_name = name[len(CHOICE_SET_PREFIX):]
            state["choice_set_reads"].append(name)
            choices = state["choices_by_field"][field_name]
            self._json({"count": 1, "results": [{
                "id": CHOICE_SET_ID, "name": name,
                "extra_choices": [[c, c] for c in choices],
            }]})
        elif path == "/api/extras/custom-fields/":
            name = self._query()["name"]
            state["field_reads"].append(name)
            if name in state["absent"]:
                self._json({"count": 0, "results": []})
            else:
                self._json({"count": 1, "results": [
                    {"id": state["ids"][name], "name": name}]})
        else:
            state["unexpected"].append(("GET", self.path))
            self._json({"detail": "not found"}, status=404)

    def do_POST(self):
        state = _MockNetBoxHandler.state
        state["posts"].append((self._path(), self._body()))
        self._json({"id": 99}, status=201)

    def do_PATCH(self):
        state = _MockNetBoxHandler.state
        state["patches"].append((self._path(), self._body()))
        self._json({"id": 1})

    def log_message(self, fmt, *args):  # keep test output quiet
        pass


@unittest.skipUnless(ANSIBLE_PLAYBOOK,
                     "ansible-playbook is not installed")
class SpecBootstrapFieldPayloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fields = _play_fields()
        _MockNetBoxHandler.state = {
            "choice_set_reads": [], "field_reads": [],
            "posts": [], "patches": [], "unexpected": [],
            "choices_by_field": {f["name"]: list(f["choices"])
                                 for f in fields if f.get("choices")},
            "ids": {f["name"]: 20 + i for i, f in enumerate(fields)},
            "absent": set(),
        }
        self.server = ThreadingHTTPServer(("localhost", 0), _MockNetBoxHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def _run(self, absent=()):
        _MockNetBoxHandler.state["absent"] = set(absent)
        with tempfile.TemporaryDirectory() as scratch:
            env = {
                k: v for k, v in os.environ.items()
                if k not in ("NETBOX_API", "NETBOX_TOKEN", "NETBOX_SITE",
                             "NETBOX_PLATFORM", "BACKUP_USER",
                             "BACKUP_PASSWORD", "BACKUP_GIT_URL",
                             "BACKUP_GIT_TOKEN", "BACKUP_WORKDIR")
            }
            env.update({
                "NETBOX_API": "http://localhost:%d" % self.server.server_port,
                "NETBOX_TOKEN": "fixture-token",
                "TMPDIR": scratch,
            })
            return subprocess.run(
                ["ansible-playbook", "-i", "inventory/empty.yml",
                 "bootstrap_netbox.yml"],
                cwd=_REPO, capture_output=True, text=True, env=env,
                timeout=300,
            )

    @staticmethod
    def _field_posts(state):
        return [body for path, body in state["posts"]
                if path == "/api/extras/custom-fields/"]

    @staticmethod
    def _field_patches(state):
        return {body["name"]: body for path, body in state["patches"]
                if path.startswith("/api/extras/custom-fields/")}

    def test_update_body_groups_every_existing_field(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        state = _MockNetBoxHandler.state
        self.assertEqual(state["unexpected"], [])
        # No field is missing, so nothing is created.
        self.assertEqual(self._field_posts(state), [])
        patches = self._field_patches(state)
        self.assertEqual(set(patches), SIX_FIELDS)
        for name, body in patches.items():
            self.assertEqual(body["group_name"], "Backup", name)

    def test_create_body_groups_the_new_field(self):
        proc = self._run([ABSENT_FIELD])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        state = _MockNetBoxHandler.state
        self.assertEqual(state["unexpected"], [])
        posts = self._field_posts(state)
        self.assertEqual([body["name"] for body in posts], [ABSENT_FIELD])
        for body in posts:
            self.assertEqual(body["group_name"], "Backup")
        # The select field keeps its choice set on create.
        self.assertEqual(posts[0]["choice_set"], CHOICE_SET_ID)
        patches = self._field_patches(state)
        self.assertEqual(set(patches), SIX_FIELDS - {ABSENT_FIELD})
        for name, body in patches.items():
            self.assertEqual(body["group_name"], "Backup", name)


if __name__ == "__main__":
    unittest.main()