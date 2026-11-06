import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch

from tidbyt_agent_alerts.api import Client
from tidbyt_agent_alerts.config import AlertError
from test_config import settings


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((self.command, self.path, self.headers, None))
        if self.server.status == 302:
            self.send_response(302)
            self.send_header("Location", self.server.target)
            self.end_headers()
            return
        self.send_response(self.server.status)
        self.end_headers()
        self.wfile.write(self.server.response)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append((self.command, self.path, self.headers, json.loads(body)))
        self.send_response(self.server.status)
        self.end_headers()
        self.wfile.write(b"App pushed.")

    do_PATCH = do_POST


class APITests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.requests = []
        self.server.status = 200
        self.server.response = b'{"id":"desk","brightness":25}'
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = Client(settings(TIDBYT_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/v0/devices"))

    def test_device_auth_and_encoded_id(self):
        self.client.device_id = "a/b ?"
        self.assertEqual(self.client.device()["id"], "desk")
        _, path, headers, _ = self.server.requests[-1]
        self.assertEqual(path, "/v0/devices/a%2Fb%20%3F")
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    def test_image_push_uses_standard_endpoint_without_installation(self):
        image = b"RIFF test image"
        self.client.push(image)
        method, path, _, payload = self.server.requests[-1]
        self.assertEqual((method, path), ("POST", "/v0/devices/desk/push"))
        self.assertEqual(base64.b64decode(payload["image"]), image)
        self.assertFalse(payload["background"])
        self.assertNotIn("installationID", payload)

    def test_brightness_patch_and_wrapped_device(self):
        self.client.brightness(30)
        method, _, _, payload = self.server.requests[-1]
        self.assertEqual((method, payload), ("PATCH", {"brightness": 30}))
        self.server.response = b'{"device":{"id":"desk"}}'
        self.assertEqual(self.client.device()["id"], "desk")

    def test_list_devices(self):
        self.server.response = b'{"devices":[{"id":"desk"},null,{}]}'
        self.assertEqual(self.client.devices(), [{"id": "desk"}])
        self.assertEqual(self.server.requests[-1][1], "/v0/devices")

    def test_http_errors_do_not_expose_response_body(self):
        self.server.status = 401
        self.server.response = b"SECRET key and private error body"
        with self.assertRaisesRegex(AlertError, "401") as caught:
            self.client.device()
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn("test-key", str(caught.exception))

    def test_redirect_does_not_forward_credentials(self):
        self.server.status = 302
        self.server.target = f"http://127.0.0.1:{self.server.server_port}/leak"
        with self.assertRaisesRegex(AlertError, "302"):
            self.client.device()
        self.assertEqual(len(self.server.requests), 1)

    def test_bad_get_payload_is_rejected(self):
        for payload in (b"not JSON", b"[]", b"{}", b'{"device":null}'):
            self.server.response = payload
            with self.subTest(payload=payload), self.assertRaises(AlertError):
                self.client.device()

    def test_timeout_is_bounded_and_redacted(self):
        with patch.object(self.client.opener, "open", side_effect=TimeoutError("SECRET")) as opened:
            with self.assertRaises(AlertError) as caught:
                self.client.device()
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertEqual(opened.call_args.kwargs["timeout"], 10)
