"""Git sync tests (spec 12.6 and 12.7).

12.6: the commit is skipped when the redacted file is unchanged — a second
run over the same content must not add a commit to the archive repository.
12.7: a URL that carries userinfo is refused.

The archive remote is a local bare repository, so no device and no token is
involved. Temporary trees live under the system scratch directory.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import _REPO

GIT_SYNC = _REPO / "scripts" / "git_sync.sh"


def _run(env_overrides, args, cwd=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("NETBOX_SITE", "NETBOX_PLATFORM", "BACKUP_GIT_URL",
                        "BACKUP_GIT_TOKEN", "BACKUP_WORKDIR")}
    env.update(env_overrides)
    return subprocess.run(["bash", str(GIT_SYNC)] + args,
                          capture_output=True, text=True, env=env, cwd=cwd)


def _commit_count(repo: Path) -> int:
    out = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
                         capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


class Spec12_6CommitSkippedWhenUnchanged(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        base = Path(self.scratch.name)
        self.bare = base / "origin.git"
        self.work = base / "work"
        subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(self.bare)], check=True)
        self.work.mkdir()
        (self.work / "site-a").mkdir()
        # A one-byte change per run keeps the content otherwise identical.
        self.device_file = self.work / "site-a" / "device-a.cfg"
        self.device_file.write_text("! device-a running config\n", encoding="utf-8")

    def tearDown(self):
        self.scratch.cleanup()

    def _env(self):
        return {"BACKUP_GIT_URL": "file://%s" % self.bare,
                "BACKUP_WORKDIR": str(self.work)}

    def test_second_run_makes_no_commit(self):
        first = _run(self._env(), ["--", "site-a/device-a.cfg"])
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("committed", first.stdout)
        self.assertEqual(_commit_count(self.bare), 1)

        second = _run(self._env(), ["--", "site-a/device-a.cfg"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotIn("committed", second.stdout)
        # Still exactly one commit: the unchanged file was skipped.
        self.assertEqual(_commit_count(self.bare), 1)

    def test_commit_message_carries_the_site_limit(self):
        first = _run({**self._env(), "NETBOX_SITE": "site-a"}, ["--", "site-a/device-a.cfg"])
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        message = subprocess.run(
            ["git", "-C", str(self.bare), "log", "-1", "--format=%s"],
            capture_output=True, text=True).stdout.strip()
        self.assertTrue(message.startswith("backup "))
        self.assertTrue(message.endswith(" site-a"))

    def test_commit_message_carries_the_platform_limit(self):
        work = self.work / "site-b"
        work.mkdir(parents=True)
        (work / "device-b.cfg").write_text("! device-b running config\n", encoding="utf-8")
        proc = _run({**self._env(), "NETBOX_PLATFORM": "platform-a"},
                    ["--", "site-b/device-b.cfg"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        message = subprocess.run(
            ["git", "-C", str(self.bare), "log", "-1", "--format=%s"],
            capture_output=True, text=True).stdout.strip()
        self.assertTrue(message.endswith(" platform-a"))

    def test_commit_message_defaults_to_all(self):
        work = self.work / "site-a"
        (work / "device-c.cfg").write_text("! device-c running config\n", encoding="utf-8")
        proc = _run(self._env(), ["--", "site-a/device-c.cfg"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        message = subprocess.run(
            ["git", "-C", str(self.bare), "log", "-1", "--format=%s"],
            capture_output=True, text=True).stdout.strip()
        self.assertTrue(message.endswith(" all"))

    def test_empty_url_keeps_files_local(self):
        (self.work / "site-a" / "device-d.cfg").write_text("! device-d\n", encoding="utf-8")
        proc = _run({"BACKUP_WORKDIR": str(self.work)}, ["--", "site-a/device-d.cfg"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("nothing is pushed", proc.stdout)
        self.assertTrue((self.work / "site-a" / "device-d.cfg").exists())

    def test_missing_file_refused(self):
        proc = _run(self._env(), ["--", "site-a/not-a-file.cfg"])
        self.assertEqual(proc.returncode, 2)

    def test_path_traversal_refused(self):
        proc = _run(self._env(), ["--", "../outside/site-a/device-a.cfg"])
        self.assertEqual(proc.returncode, 2)


class Spec12_7UserinfoRefused(unittest.TestCase):
    def test_user_pass_url_refused(self):
        proc = _run({"BACKUP_GIT_URL": "https://user:pass@example.test/repo.git",
                     "BACKUP_WORKDIR": "/nonexistent-workdir"},
                    ["--"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("credentials", proc.stderr)

    def test_user_only_url_refused(self):
        proc = _run({"BACKUP_GIT_URL": "https://token-holder@example.test/repo.git",
                     "BACKUP_WORKDIR": "/nonexistent-workdir"},
                    ["--"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("credentials", proc.stderr)

    def test_scp_style_url_refused(self):
        proc = _run({"BACKUP_GIT_URL": "someone@example.test:repo.git",
                     "BACKUP_WORKDIR": "/nonexistent-workdir"},
                    ["--"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("credentials", proc.stderr)

    def test_plain_url_not_refused_at_parsing(self):
        # A URL without userinfo gets past the parser; with no reachable
        # remote the fetch step fails, never the credential check.
        proc = _run({"BACKUP_GIT_URL": "https://example.test/repo.git",
                     "BACKUP_WORKDIR": str(Path(_REPO) / "work")}, ["--"])
        self.assertNotIn("credentials", proc.stderr)


if __name__ == "__main__":
    unittest.main()