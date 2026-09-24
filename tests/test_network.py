import socket
import ssl
import unittest
import urllib.error
from unittest.mock import patch
from agent.model import CompatibleModel, ModelError, network_error


class NetworkTests(unittest.TestCase):
    def test_permission_is_not_timeout(self):
        error = network_error(urllib.error.URLError(PermissionError(13, "denied")), .1)
        self.assertEqual(error.code, "network_denied")
        self.assertIn("PermissionError", error.diagnostic)

    def test_timeout_dns_tls_are_distinct(self):
        for reason, code in [(TimeoutError(), "timeout"), (socket.gaierror(-2, "missing"), "dns_error"),
                             (ssl.SSLError("certificate"), "tls_error"), (ConnectionRefusedError(), "connection_refused")]:
            with self.subTest(code=code):
                self.assertEqual(network_error(urllib.error.URLError(reason), .2).code, code)

    def model(self):
        return CompatibleModel({"base_url": "https://api.example.com", "model": "test", "api_key": "fake-test-only"})

    def test_transient_error_retries_same_call_once(self):
        m = self.model()
        with patch.object(m, "_call_once", side_effect=[ModelError("timeout", "timeout"), {"ok": True}]) as call, patch("agent.model.time.sleep"):
            self.assertEqual(m.call([], 5), {"ok": True})
            self.assertEqual(call.call_count, 2)

    def test_permission_and_auth_are_not_retried(self):
        for code in ["network_denied", "http_401"]:
            m = self.model()
            with patch.object(m, "_call_once", side_effect=ModelError("failure", code)) as call:
                with self.assertRaises(ModelError): m.call([], 5)
                self.assertEqual(call.call_count, 1)

    def test_retry_is_bounded(self):
        m = self.model()
        with patch.object(m, "_call_once", side_effect=ModelError("failure", "connection_error")) as call, patch("agent.model.time.sleep"):
            with self.assertRaises(ModelError): m.call([], 5)
            self.assertEqual(call.call_count, 2)


if __name__ == "__main__": unittest.main()
