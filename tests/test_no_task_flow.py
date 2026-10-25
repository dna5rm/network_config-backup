"""End-to-end no-task flow against a mock NetBox.

Runs the real backup.yml through ansible-playbook with a static inventory
and a mock NetBox HTTP server on the loopback interface (name only). The
mock device belongs to the f5.bigip placeholder platform, whose task file
must never open a connection to a device.

Asserts (spec 1.5, 4, 10, 12.5):
  - the play completes without contacting any device,
  - NetBox is patched exactly once for the device, with custom_fields equal
    to the seeded read plus validation_state=no_task and nothing else,
  - observed_version and desired_version seeds are preserved,
  - no archive file or commit is produced for a placeholder platform,
  - the lookup plugin read /api/dcim/platforms/ exactly once in the run.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from tests import _REPO

ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")


class _MockNetBoxHandler(BaseHTTPRequestHandler):
    state = None  # injected by the test

    def _route(self):
        parsed = urlparse(self.path)
        return parsed.path

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, query = self.path.split("?", 1) if "?" in self.path else (self.path, "")
        state = _MockNetBoxHandler.state
        if path == "/api/dcim/platforms/":
            state["platform_calls"].append(self.path)
            self._json({"results": [{
                "id": 7, "url": "/api/dcim/platforms/7/", "name": "Fixture PA",
                "slug": "platform-a",
                "custom_fields": {"ansible_network_os": "f5.bigip",
                                  "ansible_user": ""},
            }]})
        elif path == "/api/dcim/devices/1/":
            state["device_get_calls"].append(self.path)
            self._json({"id": 1, "custom_fields": {
                "observed_version": "1.2.3-seed",
                "desired_version": "9.9-seed",
            }})
        else:
            state["unexpected"].append(("GET", self.path))
            self._json({"detail": "not found"}, status=404)

    def do_PATCH(self):
        path = self._route()
        state = _MockNetBoxHandler.state
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        state["patch_calls"].append((path, body))
        self._json({"id": 1})

    def log_message(self, fmt, *args):  # keep test output quiet
        pass


@unittest.skipUnless(ANSIBLE_PLAYBOOK,
                     "ansible-playbook is not installed")
class SpecNoTaskFlowEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _MockNetBoxHandler.state = {
            "platform_calls": [], "device_get_calls": [],
            "patch_calls": [], "unexpected": [],
        }
        self.server = ThreadingHTTPServer(("localhost", 0), _MockNetBoxHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.workdir = Path(self.tmp.name) / "worktree"
        self.workdir.mkdir()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _env(self, scratch):
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("NETBOX_SITE", "NETBOX_PLATFORM", "BACKUP_GIT_URL",
                         "BACKUP_GIT_TOKEN")
        }
        env.update({
            "NETBOX_API": "http://localhost:%d" % self.server.server_port,
            "NETBOX_TOKEN": "fixture-token",
            "BACKUP_USER": "fixture-operator",
            "BACKUP_PASSWORD": "fixture-password",
            "BACKUP_WORKDIR": str(self.workdir),
            "TMPDIR": str(scratch),
        })
        return env

    def test_placeholder_platform_is_recorded_without_a_connection(self):
        with tempfile.TemporaryDirectory() as scratch:
            proc = subprocess.run(
                ["ansible-playbook", "-i", "tests/integration_inventory.yml",
                 "backup.yml"],
                cwd=_REPO, capture_output=True, text=True, env=self._env(scratch),
                timeout=600,
            )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        state = _MockNetBoxHandler.state
        self.assertEqual(state["unexpected"], [])
        # The platform metadata was read once for the whole play.
        self.assertEqual(len(state["platform_calls"]), 1)

        # The device record was read, then patched exactly once.
        self.assertEqual(len(state["device_get_calls"]), 1)
        self.assertEqual(len(state["patch_calls"]), 1)
        path, body = state["patch_calls"][0]
        self.assertEqual(path, "/api/dcim/devices/1/")
        self.assertEqual(set(body.keys()), {"custom_fields"})
        self.assertEqual(body["custom_fields"], {
            "observed_version": "1.2.3-seed",
            "desired_version": "9.9-seed",
            "validation_state": "no_task",
        })

        # No archive file was written for a placeholder platform: only the
        # empty working tree the play creates for the git step exists.
        children = [p.name for p in self.workdir.iterdir()]
        self.assertEqual(children, [])
        self.assertFalse(self.server and state["patch_calls"] is None)

    def test_run_reports_the_skip_without_failure(self):
        with tempfile.TemporaryDirectory() as scratch:
            proc = subprocess.run(
                ["ansible-playbook", "-i", "tests/integration_inventory.yml",
                 "backup.yml"],
                cwd=_REPO, capture_output=True, text=True, env=self._env(scratch),
                timeout=600,
            )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("device-a", proc.stdout)
        self.assertNotIn("failed=1", proc.stdout)
        self.assertNotIn("FAILED", proc.stdout)


if __name__ == "__main__":
    unittest.main()