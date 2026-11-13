"""Recovery tests for git_sync.sh against stale or empty archive workdirs.

These cover the live failure where a Semaphore rerun finds an archive
workdir that was initialised against a repository that was later
recreated empty:
  1. The workdir has files but no .git and the remote already carries
     history: the script adopts that history and pushes on top of it.
  2. The workdir has a .git with no commits and a stale origin/main
     fetched from a throwaway remote, now pointed at an empty bare
     repository: the stale tracking ref is dropped and the file is pushed.

Temporary trees live under the system scratch directory.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

GIT_SYNC = Path(__file__).resolve().parent.parent / "scripts" / "git_sync.sh"


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)


def _run(env_overrides, args):
    env = {k: v for k, v in os.environ.items()
           if k not in ("NETBOX_SITE", "NETBOX_PLATFORM", "BACKUP_GIT_URL",
                        "BACKUP_GIT_TOKEN", "BACKUP_WORKDIR")}
    env.update(env_overrides)
    return subprocess.run(["bash", str(GIT_SYNC)] + args,
                          capture_output=True, text=True, env=env)


def _commit_count(repo: Path) -> int:
    out = subprocess.run(["git", "-C", str(repo),
                          "rev-list", "--count", "HEAD"],
                         capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


class StaleWorkdirRecovery(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.base = Path(self.scratch.name)

    def tearDown(self):
        self.scratch.cleanup()

    def test_adopts_remote_history_when_workdir_lacks_git(self):
        # Remote bare repo already has one commit.
        bare = self.base / "origin.git"
        seed = self.base / "seed"
        seed.mkdir()
        _git("init", "--quiet", "--initial-branch=main", ".", cwd=seed)
        (seed / "old.txt").write_text("history\n", encoding="utf-8")
        _git("add", "old.txt", cwd=seed)
        _git("-c", "user.name=test", "-c", "user.email=test@localhost.invalid",
             "commit", "--quiet", "--message", "seed", cwd=seed)
        _git("clone", "--quiet", "--bare", str(seed), str(bare))

        # Workdir has only a cfg file, written by the play before this script.
        work = self.base / "work"
        (work / "site-a").mkdir(parents=True)
        cfg = work / "site-a" / "device-a.cfg"
        cfg.write_text("! device-a running config\n", encoding="utf-8")

        proc = _run({"BACKUP_GIT_URL": "file://%s" % bare,
                     "BACKUP_WORKDIR": str(work)},
                    ["--", "site-a/device-a.cfg"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Seed commit plus this run's backup commit.
        self.assertEqual(_commit_count(bare), 2)
        tree = subprocess.run(
            ["git", "-C", str(bare), "ls-tree", "--name-only", "-r", "HEAD"],
            capture_output=True, text=True).stdout.split()
        self.assertIn("site-a/device-a.cfg", tree)
        self.assertIn("old.txt", tree)

    def test_recovers_from_stale_tracking_ref_against_empty_remote(self):
        # Empty bare repo that stands in for the recreated-archive case.
        bare = self.base / "fresh-origin.git"
        _git("init", "--quiet", "--bare", "--initial-branch=main", str(bare))

        # Workdir: .git with no commits, plus a stale origin/main fetched
        # from a throwaway remote that no longer matches the URL.
        work = self.base / "work"
        work.mkdir()
        _git("init", "--quiet", "--initial-branch=main", ".", cwd=work)
        throwaway = self.base / "throwaway.git"
        seed = self.base / "seed"
        seed.mkdir()
        _git("init", "--quiet", "--initial-branch=main", ".", cwd=seed)
        (seed / "old.txt").write_text("stale history\n", encoding="utf-8")
        _git("add", "old.txt", cwd=seed)
        _git("-c", "user.name=test", "-c", "user.email=test@localhost.invalid",
             "commit", "--quiet", "--message", "stale", cwd=seed)
        _git("clone", "--quiet", "--bare", str(seed), str(throwaway))
        _git("remote", "add", "origin", str(throwaway), cwd=work)
        _git("fetch", "--quiet", "origin", cwd=work)
        # The workdir has no commits, and the URL now points elsewhere.
        _git("remote", "set-url", "origin", str(bare), cwd=work)

        cfg = work / "site-a" / "device-a.cfg"
        cfg.parent.mkdir()
        cfg.write_text("! device-a running config\n", encoding="utf-8")

        proc = _run({"BACKUP_GIT_URL": "file://%s" % bare,
                     "BACKUP_WORKDIR": str(work)},
                    ["--", "site-a/device-a.cfg"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        tree = subprocess.run(
            ["git", "-C", str(bare), "ls-tree", "--name-only", "-r", "HEAD"],
            capture_output=True, text=True).stdout.split()
        self.assertIn("site-a/device-a.cfg", tree)
        self.assertNotIn("old.txt", tree)


if __name__ == "__main__":
    unittest.main()
