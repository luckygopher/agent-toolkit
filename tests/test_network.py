"""Regression tests for missing Python CAs and distinct connection failures."""

from __future__ import annotations

import os
import json
import socket
import ssl
import sys
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/image-gen/scripts"))
import image_gen as client  # noqa: E402
from test_image_gen import api_service, png  # noqa: E402


class NetworkRegression(unittest.TestCase):
    def context(self, counts):
        context = Mock()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.cert_store_stats.side_effect = [{"x509_ca": count} for count in counts]
        return context

    def test_existing_python_trust_store_is_preserved(self):
        context = self.context([128])
        with patch.object(client.ssl, "create_default_context", return_value=context):
            result, source = client.tls_context()
        self.assertIs(result, context)
        self.assertEqual(source, "default")
        context.load_verify_locations.assert_not_called()

    def test_empty_python_trust_store_loads_system_bundle_without_disabling_tls(self):
        context = self.context([0, 128])
        with patch.dict(os.environ, {}, clear=True), patch.object(client.ssl, "create_default_context", return_value=context), patch.object(client.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath=None)), patch.object(client.Path, "is_file", return_value=True):
            result, source = client.tls_context()
        self.assertIs(result, context)
        self.assertEqual(source, "system_bundle")
        context.load_verify_locations.assert_called_once_with(cafile="/etc/ssl/cert.pem")
        self.assertTrue(result.check_hostname)
        self.assertEqual(result.verify_mode, ssl.CERT_REQUIRED)

    def test_explicit_user_trust_configuration_is_not_silently_replaced(self):
        context = self.context([0])
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/user/chosen/ca.pem"}, clear=True), patch.object(client.ssl, "create_default_context", return_value=context), patch.object(client.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath=None)):
            with self.assertRaises(client.ImageGenError) as result:
                client.tls_context()
        self.assertEqual(result.exception.code, "tls_configuration_error")
        context.load_verify_locations.assert_not_called()

    def test_hashed_certificate_directory_may_load_lazily(self):
        context = self.context([0])
        with patch.object(client.ssl, "create_default_context", return_value=context), patch.object(client.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath="/trusted/hashed")):
            _, source = client.tls_context()
        self.assertEqual(source, "default")
        context.load_verify_locations.assert_not_called()

    def test_missing_all_trust_sources_produces_actionable_error(self):
        context = self.context([0])
        with patch.dict(os.environ, {}, clear=True), patch.object(client.ssl, "create_default_context", return_value=context), patch.object(client.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath=None)), patch.object(client.Path, "is_file", return_value=False):
            with self.assertRaises(client.ImageGenError) as result:
                client.tls_context()
        self.assertEqual(result.exception.code, "tls_configuration_error")
        self.assertIn("SSL_CERT_FILE", str(result.exception))

    def test_dns_certificate_permission_and_timeout_are_distinct(self):
        cases = [
            (socket.gaierror(-2, "no name"), "dns_error"),
            (ssl.SSLCertVerificationError("untrusted issuer"), "tls_certificate_error"),
            (PermissionError("blocked"), "network_permission_denied"),
            (TimeoutError("timed out"), "timeout"),
            (ConnectionRefusedError("refused"), "connection_refused"),
        ]
        for reason, expected in cases:
            with self.subTest(expected=expected):
                result = client.connection_error(urllib.error.URLError(reason))
                self.assertEqual(result.code, expected)

    def test_generation_transport_failure_is_not_retried(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(ssl.SSLCertVerificationError("untrusted issuer"))
        context = ssl.create_default_context()
        cfg = {"base_url": "https://not-called.invalid/v1", "api_key": "dummy-private-key", "timeout": 10}
        with patch.object(client, "tls_context", return_value=(context, "default")), patch.object(client.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(client.ImageGenError) as result:
                client.post_api(cfg, "/images/generations", b"{}", "application/json")
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(result.exception.code, "tls_certificate_error")
        self.assertNotIn("dummy-private-key", str(result.exception))

    def test_local_http_doctor_does_not_require_ca_certificates(self):
        with api_service() as (base, records), patch.object(client, "load_config", return_value=({"base_url": base + "/v1"}, {})), patch.object(client, "tls_context", side_effect=AssertionError("HTTP must not require TLS")):
            result = client.doctor()
        self.assertTrue(result["reachable"])
        self.assertFalse(result["tls_verified"])
        self.assertEqual(result["certificate_source"], "not_required")
        self.assertEqual(records[0][0], "HEAD")

    def test_local_http_generation_does_not_require_ca_certificates(self):
        with api_service() as (base, records), patch.object(client, "tls_context", side_effect=AssertionError("HTTP must not require TLS")):
            result = client.post_api({"base_url": base + "/v1", "api_key": "dummy-private-key", "timeout": 10}, "/images/generations", json.dumps({"prompt": "test"}).encode(), "application/json")
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(records[0][0], "POST")

    def test_local_http_image_download_does_not_require_ca_certificates(self):
        with api_service() as (base, records), patch.object(client, "tls_context", side_effect=AssertionError("HTTP must not require TLS")):
            blob = client.output_bytes({"url": base + "/image.png"}, {"timeout": 10})
        self.assertEqual(blob, png())
        self.assertNotIn("Authorization", records[0][2])

    def test_https_handler_loads_verified_context_when_https_is_encountered(self):
        context = ssl.create_default_context()
        handler = client.VerifiedHTTPSHandler()
        request = client.urllib.request.Request("https://not-called.invalid/image.png")
        with patch.object(client, "tls_context", return_value=(context, "system_bundle")) as resolve, patch.object(handler, "do_open", return_value="response") as open_request:
            result = handler.https_open(request)
        resolve.assert_called_once()
        self.assertEqual(handler.certificate_source, "system_bundle")
        self.assertEqual(result, "response")
        self.assertIs(open_request.call_args.kwargs["context"], context)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
