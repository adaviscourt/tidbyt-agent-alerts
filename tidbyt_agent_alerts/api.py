"""Standard Tidbyt image API, also supported by Tronbyt."""

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import AlertError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API key to a redirect target.
        return None


class Client:
    def __init__(self, values):
        self.base = values["TIDBYT_BASE_URL"]
        self.key = values["TIDBYT_API_KEY"]
        self.device_id = values["TIDBYT_DEVICE_ID"]
        self.opener = build_opener(NoRedirect)

    def request(self, method, suffix="", body=None, *, collection=False):
        url = self.base if collection else self.base + "/" + quote(self.device_id, safe="")
        url += suffix
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.key,
            "Content-Type": "application/json",
        })
        try:
            with self.opener.open(req, timeout=10) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as error:
            code = error.code
            error.close()
            raise AlertError(f"Device API returned HTTP {code}. Check credentials and API URL.") from None
        except (URLError, TimeoutError, OSError):
            raise AlertError("Device API is unreachable; check the URL, network, and TLS certificate.") from None
        if len(raw) > 1024 * 1024:
            raise AlertError("Device API response is too large.")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            if method == "GET":
                raise AlertError("Device API did not return JSON. Check the API base URL.") from None
            return {}

    def devices(self):
        result = self.request("GET", collection=True)
        devices = result.get("devices", []) if isinstance(result, dict) else []
        if not isinstance(devices, list):
            raise AlertError("Device API returned an invalid device list.")
        return [device for device in devices if isinstance(device, dict) and device.get("id")]

    def device(self):
        result = self.request("GET")
        if isinstance(result, dict):
            result = result.get("device", result)
        if not isinstance(result, dict) or not result.get("id"):
            raise AlertError("Device API returned an invalid device. Check the device ID.")
        return result

    def brightness(self, value):
        self.request("PATCH", body={"brightness": value})

    def push(self, image):
        # No installationID: show once, without adding a permanent rotation entry.
        self.request("POST", "/push", {
            "deviceID": self.device_id,
            "image": base64.b64encode(image).decode("ascii"),
            "background": False,
        })
