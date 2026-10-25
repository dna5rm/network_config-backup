"""Tests for the uri status portability filter.

Different ansible-core versions register the HTTP status of a uri task
under different keys; the filter accepts both and returns 0 otherwise.
"""

import unittest

from filter_plugins.http_status import config_backup_http_status


class HttpStatusFilter(unittest.TestCase):
    def test_reads_the_current_key(self):
        self.assertEqual(config_backup_http_status({"status": 200}), 200)
        self.assertEqual(config_backup_http_status({"status": 404}), 404)

    def test_reads_the_legacy_key(self):
        self.assertEqual(config_backup_http_status({"status_code": 200}), 200)

    def test_prefers_the_current_key(self):
        self.assertEqual(
            config_backup_http_status({"status": 502, "status_code": 200}), 502)

    def test_zero_when_absent(self):
        self.assertEqual(config_backup_http_status({"json": {}}), 0)
        self.assertEqual(config_backup_http_status({}), 0)
        self.assertEqual(config_backup_http_status("not a dict"), 0)
        self.assertEqual(config_backup_http_status(None), 0)


if __name__ == "__main__":
    unittest.main()