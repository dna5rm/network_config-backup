"""Redaction filter tests (spec 12.3 and the section 8 fail-closed rule).

Spec 12.3 needs a fixture config that still matches a secret rule after
redaction; these tests assert that the leak backstop fires on those
fixtures, that clean fixtures report no leak, and that every platform rule
list is non-empty (a platform with an empty rule list must never write a
file).
"""

import unittest
from pathlib import Path

from filter_plugins import ios_redact as ios_plugin
from filter_plugins import junos_redact as junos_plugin
from filter_plugins import nxos_redact as nxos_plugin
from filter_plugins import panos_redact as panos_plugin
from tests import _REPO, _fixture

IOS = ios_plugin
JUNOS = junos_plugin
NXOS = nxos_plugin
PANOS = panos_plugin


class PlatformRuleListsAreNonEmpty(unittest.TestCase):
    def test_ios_rules(self):
        self.assertGreater(len(IOS.REDACT_RULES), 0)

    def test_nxos_rules(self):
        self.assertGreater(len(NXOS.REDACT_RULES), 0)

    def test_junos_rules(self):
        self.assertGreater(len(JUNOS.REDACT_RULES), 0)

    def test_panos_rules(self):
        self.assertGreater(len(PANOS.REDACT_RULES), 0)


class Spec12_3LeakBackstop(unittest.TestCase):
    """A fixture config that still matches a secret rule after redaction."""

    def test_ios_fixture_still_leaks_after_redaction(self):
        redacted = IOS.ios_redact(_fixture("ios_leaks.cfg"))
        self.assertEqual(IOS.ios_leaks(redacted), ["salted password hash token"])

    def test_nxos_fixture_still_leaks_after_redaction(self):
        redacted = NXOS.nxos_redact(_fixture("nxos_leaks.cfg"))
        self.assertEqual(NXOS.nxos_leaks(redacted), ["type-7 key material"])

    def test_junos_fixture_still_leaks_after_redaction(self):
        redacted = JUNOS.junos_redact(_fixture("junos_leaks.cfg"))
        self.assertEqual(JUNOS.junos_leaks(redacted),
                         ["Junos encrypted password token"])

    def test_panos_fixture_still_leaks_after_redaction(self):
        redacted = PANOS.panos_redact(_fixture("panos_leaks.cfg"))
        self.assertEqual(PANOS.panos_leaks(redacted), ["forty-hex key material"])


class CleanFixturesPass(unittest.TestCase):
    def test_ios_clean(self):
        self.assertEqual(IOS.ios_leaks(IOS.ios_redact(_fixture("ios_clean.cfg"))), [])

    def test_nxos_clean(self):
        self.assertEqual(NXOS.nxos_leaks(NXOS.nxos_redact(_fixture("nxos_clean.cfg"))), [])

    def test_junos_clean(self):
        self.assertEqual(JUNOS.junos_leaks(JUNOS.junos_redact(_fixture("junos_clean.cfg"))), [])

    def test_panos_clean(self):
        self.assertEqual(PANOS.panos_leaks(PANOS.panos_redact(_fixture("panos_clean.cfg"))), [])


class RedactionBehaviour(unittest.TestCase):
    def test_ios_redacts_credential_lines(self):
        config = ("hostname device-a\n"
                  "enable secret 5 $1$mERr$hx5rVt7rPNoS4Fvb8KewP/\n"
                  "snmp-server community not-a-real-community RO\n"
                  "snmp-server location site-a\n"
                  "end\n")
        out = IOS.ios_redact(config)
        self.assertNotIn("$1$mERr", out)
        self.assertNotIn("not-a-real-community", out)
        self.assertIn("snmp-server location site-a", out)
        self.assertIn("hostname device-a", out)

    def test_ios_placeholder_replaces_the_whole_line(self):
        self.assertEqual(IOS.ios_redact("  username backup secret 5 xyz\n"),
                         "! <sensitive line removed>\n")

    def test_ios_redaction_is_idempotent(self):
        source = _fixture("ios_leaks.cfg")
        self.assertEqual(IOS.ios_redact(IOS.ios_redact(source)), IOS.ios_redact(source))

    def test_junos_placeholder_marker(self):
        config = 'set system root-authentication encrypted-password "$5$salt$hashvalue"'
        out = JUNOS.junos_redact(config)
        self.assertEqual(out, JUNOS.PLACEHOLDER)

    def test_junos_reacts_to_pre_shared_key_lines(self):
        config = ("set security ike policy gateway-a pre-shared-key ascii-text "
                  '"$9$AbcdefGhIjkLmnoPqrst"')
        out = JUNOS.junos_redact(config)
        self.assertEqual(out, JUNOS.PLACEHOLDER)

    def test_panos_redacts_phash_lines(self):
        config = ("set deviceconfig system hostname device-a\n"
                  "set mgt-config users admin phash a1b2c3d4e5\n")
        out = PANOS.panos_redact(config)
        self.assertIn("hostname device-a", out)
        self.assertNotIn("a1b2c3d4e5", out)
        self.assertIn(PANOS.PLACEHOLDER, out)

    def test_panos_keeps_non_secret_lines(self):
        config = ("set deviceconfig system hostname device-a\n"
                  "set deviceconfig system password-recovery yes\n")
        out = PANOS.panos_redact(config)
        self.assertIn("password-recovery yes", out)

    def test_leak_labels_never_carry_line_content(self):
        # The failure message names the rule id, not the secret.
        for leaks, token in [
            (IOS.ios_leaks(IOS.ios_redact(_fixture("ios_leaks.cfg"))), "zyxwvutsrqponmlk"),
            (NXOS.nxos_leaks(NXOS.nxos_redact(_fixture("nxos_leaks.cfg"))), "08224F4D1D0A0A"),
            (JUNOS.junos_leaks(JUNOS.junos_redact(_fixture("junos_leaks.cfg"))), "Gy5ZzAbcdefGHijkl"),
            (PANOS.panos_leaks(PANOS.panos_redact(_fixture("panos_leaks.cfg"))), "0123456789abcdef"),
        ]:
            for label in leaks:
                self.assertNotIn(token, label)


class SyntheticEwcType7ConfigIsFullyRedacted(unittest.TestCase):
    """A synthetic EWC AP config with fake type-7 tokens, no live secret.

    The embedded wireless controller configuration carries type-7 material
    on command lines that do not start with 'key' (the password-encryption
    master key, a per-wlan psk line, mid-line legacy tokens, the
    self-signed certificate identity number); redaction
    must replace those whole lines or the leak backstop fails the run.
    """

    FIXTURE = "ios_ewc_type7.cfg"

    def test_ios_ewc_type7_lines_are_replaced_whole(self):
        redacted = IOS.ios_redact(_fixture(self.FIXTURE))
        for token in ("08224F4D1D0A0A0A", "135A4C1A0D1A0A0A0A", "123A0C1607190A0A0A",
                      "1234567890"):
            self.assertNotIn(token, redacted)
        self.assertNotIn("key config-key", redacted)
        self.assertNotIn("TP-self-signed", redacted)
        self.assertNotIn("psk ascii", redacted)

    def test_ios_ewc_type7_reports_no_leak_after_redaction(self):
        redacted = IOS.ios_redact(_fixture(self.FIXTURE))
        self.assertEqual(IOS.ios_leaks(redacted), [])

    def test_ios_ewc_type7_keeps_the_non_secret_lines(self):
        redacted = IOS.ios_redact(_fixture(self.FIXTURE))
        self.assertIn("hostname device-a", redacted)
        self.assertIn("wlan fixture-ssid 2 fixture-ssid", redacted)
        self.assertIn("snmp-server location site-a", redacted)

    def test_ios_replaces_a_whole_line_carrying_type7_material(self):
        line = " description legacy key 7 0123456789ABCDEF\n"
        self.assertEqual(IOS.ios_redact(line), IOS.PLACEHOLDER + "\n")


if __name__ == "__main__":
    unittest.main()