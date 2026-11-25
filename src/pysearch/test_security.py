import unittest

from pysearch.security import host_is_public, is_public_ip, url_is_public


class SecurityTests(unittest.TestCase):
    def test_non_public_addresses_rejected(self):
        for bad in (
            "http://127.0.0.1/", "http://localhost:8080/admin", "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/", "http://100.64.0.1/",
            "http://0.0.0.0/", "http://[::1]/", "http://[fe80::1]/", "http://[fc00::1]/",
            "http://[::ffff:127.0.0.1]/", "http://metadata.google.internal/", "http://printer.local/",
            "http://intranet/", "http://2130706433/", "http://0x7f.0.0.1/", "http://017700000001/",
        ):
            self.assertFalse(url_is_public(bad), bad)

    def test_public_addresses_allowed(self):
        for good in ("https://example.com/", "http://93.184.216.34/", "https://sub.docs.python.org/3/",
                     "http://[2606:2800:220:1:248:1893:25c8:1946]/"):
            self.assertTrue(url_is_public(good), good)

    def test_helpers(self):
        self.assertFalse(host_is_public(None))
        self.assertFalse(host_is_public(""))
        self.assertTrue(is_public_ip("8.8.8.8"))
        self.assertFalse(is_public_ip("::ffff:10.1.2.3"))


if __name__ == "__main__":
    unittest.main()
