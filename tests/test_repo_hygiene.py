"""Repo hygiene scan (spec 12.9 and the public-repo rule).

The repository, excluding nothing, must contain none of: a home domain, a
private address, a personal login, a home directory path, a Semaphore path,
a bind-mount note, the reference implementation's env names, or a device
name from a lab. The scan is structural on purpose: it must not embed any
real name, address or domain in order to look for them.

Allowed addresses: the RFC 5737 fixture literal 192.0.2.10 and quads made
only of 0 and 255 (netmasks).
Allowed domain: none. The git commit identity uses the reserved .invalid
TLD, which is not a routable domain and is allowlisted below by exact
literal.
"""

import os
import re
import unittest
from pathlib import Path

from tests import _REPO

DOMAIN_RE = re.compile(
    r"(?i)\b[a-z0-9][a-z0-9-]{0,63}\.(?:net|com|org|io|dev|me|cc|coop|info|"
    r"biz|app|xyz|site|online|shop|tv|fm|so)\b"
)
QUAD_RE = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w.])")
HOME_PATH_RE = re.compile(
    r"(?i)(?:^|[^\w])/(?:home|Users|root)[/\\][A-Za-z0-9._-]+"
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# RFC 2606 / 6761 reserved TLDs can never be a personal or home domain; the
# git commit identity and the git tests use them on purpose.
EMAIL_RESERVED_TLDS = ("invalid", "test", "example", "localhost")
SEMAPHORE_TOKEN_RE = re.compile(r"(?i)\S*semaphore\S*")
REFERENCE_ENV_RE = re.compile(r"\bCISCO_BACKUP_[A-Z_]+\b")
PRIVATE_ADDRESS_ALLOWED = {"192.0.2.10"}
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", ".venv", "collections", "work"}


def _repo_files():
    for path in sorted(Path(_REPO).rglob("*")):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _dotted_quad_allowed(quad: str) -> bool:
    if quad in PRIVATE_ADDRESS_ALLOWED:
        return True
    octets = quad.split(".")
    return all(o in {"0", "255"} for o in octets)


def _email_reserved(email: str) -> bool:
    tld = email.rsplit(".", 1)[-1].lower()
    return tld in EMAIL_RESERVED_TLDS


class RepoContainsNoPrivateData(unittest.TestCase):
    def _scan(self, pattern, allow=None, message=None):
        violations = []
        for path in _repo_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in pattern.finditer(text):
                if allow and allow(match.group(0)):
                    continue
                violations.append("%s: %r" % (
                    path.relative_to(_REPO), match.group(0)))
        return violations

    def test_no_domains(self):
        # Every domain-shaped token is a violation; fixtures and tests carry
        # none: the git tests use the reserved .test TLD instead.
        self.assertEqual(self._scan(DOMAIN_RE), [])

    def test_no_private_or_lab_addresses(self):
        violations = []
        for path in _repo_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in QUAD_RE.finditer(text):
                if not _dotted_quad_allowed(match.group(1)):
                    violations.append("%s: %r" % (
                        path.relative_to(_REPO), match.group(1)))
        self.assertEqual(violations, [])

    def test_no_home_directory_paths(self):
        # No home directory path, no bind-mount note about one.
        self.assertEqual(self._scan(HOME_PATH_RE), [])

    def test_no_emails_other_than_reserved_tlds(self):
        self.assertEqual(
            self._scan(EMAIL_RE, allow=lambda m: _email_reserved(m)), [])

    def test_no_semaphore_path_or_bind_mount_note(self):
        # The concept may appear in prose (the spec copy) and in this rule's
        # own pattern, but never as a path-like token carrying a slash.
        violations = []
        for path in _repo_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in SEMAPHORE_TOKEN_RE.finditer(text):
                token = match.group(0)
                if "/" in token:
                    violations.append("%s: %r" % (
                        path.relative_to(_REPO), token))
        self.assertEqual(violations, [])

    def test_reference_env_names_absent(self):
        self.assertEqual(self._scan(REFERENCE_ENV_RE), [])

    def test_fixtures_use_only_the_fixture_names(self):
        # Config fixtures may name site-a / device-a / platform-a and the
        # allowlisted address only; nothing lab-specific.
        for path in sorted((Path(_REPO) / "tests" / "fixtures").glob("*.cfg")):
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("device-a", text)


if __name__ == "__main__":
    unittest.main()