"""Bootstrap check-mode test (spec 12.8).

Running bootstrap_netbox.yml in check mode against an empty inventory must
report the six custom fields and no seventh, and no NetBox call is needed.
Skipped with a clear reason when ansible-playbook is not installed.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import _REPO

SIX_FIELDS = {
    "ansible_network_os",
    "ansible_user",
    "observed_version",
    "observed_at",
    "validation_state",
    "desired_version",
}

ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")


@unittest.skipUnless(ANSIBLE_PLAYBOOK,
                     "ansible-playbook is not installed")
class Spec12_8BootstrapCheckMode(unittest.TestCase):
    def _check_run(self):
        with tempfile.TemporaryDirectory() as scratch:
            env = {
                k: v for k, v in os.environ.items()
                if k not in ("NETBOX_API", "NETBOX_TOKEN", "BACKUP_USER",
                             "BACKUP_PASSWORD", "NETBOX_SITE", "NETBOX_PLATFORM")
            }
            env["TMPDIR"] = scratch
            return subprocess.run(
                ["ansible-playbook", "--check", "-i", "inventory/empty.yml",
                 "bootstrap_netbox.yml"],
                cwd=_REPO, capture_output=True, text=True, env=env,
                timeout=300,
            )

    def test_check_mode_reports_exactly_six_fields(self):
        proc = self._check_run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        reported = set()
        for line in proc.stdout.splitlines():
            # The play prints: "msg": "custom_field <name> object=... type=..."
            if '"msg": "custom_field ' in line:
                reported.add(line.split('"msg": "custom_field ')[1].split()[0])
        self.assertEqual(reported, SIX_FIELDS)
        # No seventh field was reported.
        self.assertNotIn("custom_field secret", proc.stdout)
        self.assertNotIn("custom_field serial", proc.stdout)


if __name__ == "__main__":
    unittest.main()