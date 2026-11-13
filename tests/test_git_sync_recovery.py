"""Recovery tests for git_sync.sh against stale or empty archive workdirs.

These cover the live failure where a Semaphore rerun finds an archive
workdir that was initialised against a repository that was later
recreated empty:
  1. The workdir has files but no .git and the remote already carries
     history: the script adopts that history and pushes on top of it.
  3. A workdir merely behind origin/main fast-forwards and pushes.

Cases already covered:
  1. The workdir has a .git with no commits and a stale origin/main
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


class BehindOrUnrelatedHistory(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.base = Path(self.scratch.name)
        self.ident = ("-c", "user.name=test", "-c", "user.email=test@localhost.invalid")

    def tearDown(self):
        self.scratch.cleanup()

    def _seed(self, path: Path, message: str, files: dict) -> None:
        path.mkdir(parents=True)
        _git("init", "--quiet", "--initial-branch=main", ".", cwd=path)
        for name, text in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        _git("add", ".", cwd=path)
        _git(*self.ident, "commit", "--quiet", "--message", message, cwd=path)

    def test_behind_origin_fast_forwards_and_pushes_new_cfg(self):
        # Bare origin carries two commits; the workdir was cloned at the
        # first one and holds an untracked cfg file from this run.
        bare = self.base / "origin.git"
        seed = self.base / "seed"
        self._seed(seed, "first", {"old.txt": "old config\n"})
        _git("clone", "--quiet", "--bare", str(seed), str(bare))
        _git(*self.ident, "commit", "--quiet", "--message", "second",
             "--allow-empty", cwd=seed)
        _git("push", "--quiet", str(bare), "main", cwd=seed)

        work = self.base / "work"
        _git("clone", "--quiet", str(seed), str(work))
        _git("reset", "--quiet", "--hard", "HEAD~1", cwd=work)
        cfg = work / "site-a" / "device-a.cfg"
        cfg.parent.mkdir()
        cfg.write_text("! device-a running config\n", encoding="utf-8")

        proc = _run({"BACKUP_GIT_URL": "file://%s" % bare,
                     "BACKUP_WORKDIR": str(work)},
                    ["--", "site-a/device-a.cfg"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_commit_count(bare), 3)
        tree = subprocess.run(
            ["git", "-C", str(bare), "ls-tree", "--name-only", "-r", "HEAD"],
            capture_output=True, text=True).stdout.split()
        self.assertIn("site-a/device-a.cfg", tree)
        self.assertIn("old.txt", tree)
        self.assertNotIn("nothing", tree)

    def test_unrelated_history_stops_without_force_push(self):
        # Two repos with separate roots: the origin tip must survive and
        # the script must say "no common history".
        bare = self.base / "origin.git"
        remote_src = self.base / "remote-src"
        self._seed(remote_src, "remote root", {"old.txt": "remote history\n"})
        _git("clone", "--quiet", "--bare", str(remote_src), str(bare))
        tip_before = subprocess.run(
            ["git", "-C", str(bare), "rev-parse", "main"],
            capture_output=True, text=True).stdout.strip()

        work = self.base / "work"
        self._seed(work, "local root", {"site-a/device-a.cfg": "! local\n"})
        _git("remote", "add", "origin", str(bare), cwd=work)

        proc = _run({"BACKUP_GIT_URL": "file://%s" % bare,
                     "BACKUP_WORKDIR": str(work)},
                    ["--", "site-a/device-a.cfg"])
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("no common history", proc.stderr)
        tip_after = subprocess.run(
            ["git", "-C", str(bare), "rev-parse", "main"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(tip_before, tip_after)


if __name__ == "__main__":
    unittest.main()
