"""Tests for safe HTTP server bind-host validation."""
import unittest

from network_utils import validate_bind_host


class TestValidateBindHost(unittest.TestCase):
    def test_accepts_localhost_and_loopback(self):
        for host in ("localhost", "LOCALHOST", "127.0.0.1", "127.22.4.9", "::1"):
            with self.subTest(host=host):
                self.assertEqual(validate_bind_host(host), host)

    def test_accepts_explicit_private_lan_addresses(self):
        for host in ("10.12.0.7", "172.16.0.1", "172.31.255.254",
                     "192.168.1.20", "fd12:3456:789a::1"):
            with self.subTest(host=host):
                self.assertEqual(validate_bind_host(host), host)

    def test_rejects_wildcard_addresses(self):
        for host in ("0.0.0.0", "::"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_bind_host(host)

    def test_rejects_public_or_non_private_addresses(self):
        for host in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888",
                     "169.254.10.2", "192.0.2.1", "100.64.0.1"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_bind_host(host)

    def test_rejects_unknown_hostnames_and_malformed_values(self):
        for host in ("example.com", "my-lan-host", "", " 127.0.0.1", "127.0.0.1 "):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_bind_host(host)

    def test_rejects_scoped_ipv6(self):
        with self.assertRaises(ValueError):
            validate_bind_host("fe80::1%eth0")


if __name__ == "__main__":
    unittest.main()
