import json
import os
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .model import require


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Api:
    def __init__(self, origin, auth=None, ca=None):
        parsed = urlsplit(origin)
        require(parsed.scheme == "https" and parsed.hostname and not parsed.username
                and not parsed.password and parsed.path in ("", "/")
                and not parsed.query and not parsed.fragment, "Configure an HTTPS origin")
        self.origin = origin.rstrip("/")
        self.auth = auth
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=ca)))

    def call(self, method, path, data=None, missing=False):
        require(path.startswith("/") and not path.startswith("//"), "Invalid API path")
        headers = {"Accept": "application/json"}
        if self.auth:
            headers["Authorization"] = self.auth
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.origin + path, json.dumps(data).encode() if data is not None else None,
                          headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(8_000_001)
                require(len(body) <= 8_000_000, "API response exceeded limit")
                return json.loads(body) if body else None
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError(f"{method} {path.split('?')[0]} returned HTTP {error.code}") from None


class Bam(Api):
    def __init__(self):
        super().__init__(os.environ["BAM_URL"], ca=os.environ.get("BAM_CA_FILE") or None)
        result = self.bam("POST", "/sessions", {
            "username": os.environ["BAM_USER"], "password": os.environ["BAM_PASSWORD"],
        })
        self.auth = "Basic " + result["basicAuthenticationCredentials"]
        self.session_id = int(result["id"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.bam("PATCH", f"/sessions/{self.session_id}", {"state": "TERMINATED"})
        except Exception:
            print("BAM session cleanup failed; check its configured expiry")

    def bam(self, method, path, data=None, missing=False):
        return self.call(method, "/api/v2" + path, data, missing)

    def records(self, zone_id, owner):
        query = urlencode({"filter": f"absoluteName:eq('{owner}')", "limit": 100})
        records = self.bam("GET", f"/zones/{zone_id}/resourceRecords?{query}")
        rows = records["data"]
        require(isinstance(rows, list) and len(rows) < 100 and not records.get("_links", {}).get("next"),
                "Owner lookup was truncated")
        require(all(row["absoluteName"].lower().rstrip(".") == owner for row in rows),
                "BAM lookup returned a different owner")
        return rows
