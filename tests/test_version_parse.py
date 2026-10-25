"""Version parse tests (spec 12.4).

A fixture 'show version' output that does not match the parser must yield
an empty token: the play then writes validation_state unparsed and omits
observed_version from the NetBox payload, so a seeded observed_version
stays untouched. Matching fixtures return the token only.
"""

import unittest

from tests import _REPO, _fixture
from tests._filters import FILTERS, PLATFORMS

REPO_PATH = _REPO


class Spec12_4VersionParse(unittest.TestCase):
    def test_matching_fixture_yields_token_only(self):
        expected = {
            "ios": "17.09.04a",
            "nxos": "9.10(3)",
            "junos": "21.4R3-S4.9",
            "panos": "10.1.9",
        }
        for platform, want in expected.items():
            with self.subTest(platform=platform):
                fixture = ("ios_show_version.txt" if platform == "ios" else
                           "nxos_show_version.txt" if platform == "nxos" else
                           "junos_show_version.txt" if platform == "junos" else
                           "panos_show_system_info.txt")
                parse = FILTERS[platform]
                self.assertEqual(parse(_fixture(fixture)), want)

    def test_unmatched_fixture_yields_empty_token(self):
        fixture = {
            "ios": "ios_show_version_unmatched.txt",
            "nxos": "nxos_show_version_unmatched.txt",
            "junos": "junos_show_version_unmatched.txt",
            "panos": "panos_show_system_info_unmatched.txt",
        }
        for platform, name in fixture.items():
            with self.subTest(platform=platform):
                self.assertEqual(FILTERS[platform](_fixture(name)), "")

    def test_empty_and_none_input(self):
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                self.assertEqual(FILTERS[platform](""), "")
                self.assertEqual(FILTERS[platform](None), "")

    def test_panos_json_one_line_yields_token(self):
        blob = '{"response":{"result":{"system":{"sw-version":"12.2.4","model":"PA-440"}}}}'
        self.assertEqual(FILTERS["panos"](blob), "12.2.4")

    def test_version_token_is_not_the_whole_response(self):
        # Spec section 7: do not store the whole show output in NetBox.
        token = FILTERS["panos"](_fixture("panos_show_system_info.txt"))
        self.assertNotIn("model", token)
        self.assertNotIn("\n", token)


if __name__ == "__main__":
    unittest.main()