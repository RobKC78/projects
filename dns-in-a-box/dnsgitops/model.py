import ipaddress
import json
import re
from datetime import datetime, timezone
from pathlib import Path

TYPES = {"A", "AAAA", "CNAME", "TXT", "MX", "SRV", "PTR", "CAA"}
LINKED = {"CNAME", "MX", "SRV"}
KEY = r"[A-Z][A-Z0-9]+-[1-9][0-9]*"
LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decode(text):
    def unique(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), "Duplicate JSON key")
        return result

    return json.loads(text, object_pairs_hook=unique)


def load(path):
    return decode(Path(path).read_text(encoding="utf-8"))


def fqdn(value, wildcard=False):
    require(isinstance(value, str), "DNS name must be a string")
    value = value.lower().removesuffix(".")
    labels = value.split(".")
    if wildcard and labels[0] == "*":
        labels = labels[1:]
    require(len(labels) >= 2 and len(value) <= 253
            and all(re.fullmatch(LABEL, item) for item in labels), "Invalid DNS name")
    return value


def number(value, low, high, field):
    require(type(value) is int and low <= value <= high, f"Invalid {field}")
    return value


def shape(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys.split()),
            f"Expected exactly these fields: {keys}")


def state(kind, item, current=False):
    shape(item, "ttl data" + (" id" if current else ""))
    data = item["data"]
    ttl = number(item["ttl"], 0 if current else 60, 2147483647 if current else 86400, "TTL")
    if kind in {"A", "AAAA"}:
        shape(data, "addresses")
        addresses = data["addresses"]
        require(isinstance(addresses, list) and 1 <= len(addresses) <= 16, "Supply 1–16 addresses")
        parsed = [ipaddress.ip_address(value) for value in addresses]
        require(all(isinstance(value, str) for value in addresses), "Use IP address strings")
        require(all(ip.version == (4 if kind == "A" else 6) for ip in parsed), "Wrong IP family")
        require(len(set(parsed)) == len(parsed), "Duplicate IP address")
        data = {"addresses": sorted(str(ip) for ip in parsed)}
    elif kind == "TXT":
        shape(data, "text")
        require(isinstance(data["text"], str) and 1 <= len(data["text"]) <= 255
                and all(32 <= ord(c) <= 126 for c in data["text"]), "Use 1–255 printable ASCII bytes")
    elif kind == "CAA":
        shape(data, "flags tag value")
        require(type(data["flags"]) is int and data["flags"] in {0, 128}, "Invalid CAA flags")
        require(data["tag"] in ("issue", "issuewild", "iodef"), "Invalid CAA tag")
        require(isinstance(data["value"], str) and 1 <= len(data["value"]) <= 255
                and all(32 <= ord(c) <= 126 for c in data["value"]), "Invalid CAA value")
    else:
        fields = {"CNAME": "", "PTR": "", "MX": " priority", "SRV": " priority weight port"}
        shape(data, "target" + (" target_id" if kind in LINKED else "") + fields[kind])
        data = {**data, "target": fqdn(data["target"])}
        if kind in LINKED:
            number(data["target_id"], 1, 2**63-1, "linked BAM object ID")
        for field in fields[kind].split():
            number(data[field], 0, 65535, field)
    result = {"ttl": ttl, "data": data}
    if current:
        result["id"] = number(item["id"], 1, 2**63-1, "BAM object ID")
    return result


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value),
            "Use UTC timestamps YYYY-MM-DDTHH:MM:SSZ")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(req, zones):
    shape(req, "issue_key operation zone_key owner type expected desired window summary")
    require(isinstance(req["issue_key"], str) and re.fullmatch(KEY, req["issue_key"]), "Invalid Jira key")
    require(isinstance(req["summary"], str) and 0 < len(req["summary"]) <= 255
            and req["summary"].strip() and req["summary"].isprintable(), "Invalid summary")
    require(isinstance(req["zone_key"], str), "Invalid zone key")
    zone = zones.get(req["zone_key"])
    require(isinstance(zone, dict) and zone.get("enabled") is True, "Zone is not enabled")
    number(zone.get("zone_id"), 1, 2**63-1, "configured zone ID")
    number(zone.get("view_id"), 1, 2**63-1, "configured view ID")
    servers = zone.get("authorities")
    require(isinstance(servers, list) and servers and all(isinstance(s, str) for s in servers),
            "Configure authoritative server IPs")
    for server in servers:
        ipaddress.ip_address(server)
    kind, op = req["type"], req["operation"]
    require(isinstance(kind, str) and kind in TYPES and kind in zone["types"], "Record type is outside the zone policy")
    require(op in ("create", "update", "delete"), "Invalid operation")
    origin, owner = fqdn(zone["fqdn"]), fqdn(req["owner"], wildcard=True)
    require(owner == origin or owner.endswith("." + origin), "Owner is outside the configured zone")
    relative = owner[:-(len(origin)+1)] if owner != origin else ""
    require("." not in relative or (kind == "SRV" and re.fullmatch(r"_[^.]+\._(?:tcp|udp)\.[^.]+|_[^.]+\._(?:tcp|udp)", relative))
            or kind == "PTR" or (kind == "TXT" and relative.startswith("_")),
            "Use the most specific managed zone for this owner")
    require(not owner.startswith("*.") or zone.get("allow_wildcard") is True, "Wildcard requires zone opt-in")
    require(owner != origin or (kind in {"TXT", "MX", "CAA"} and zone.get("allow_apex") is True),
            "Apex records require explicit opt-in for TXT, MX or CAA")
    reverse = origin.endswith((".in-addr.arpa", ".ip6.arpa"))
    require(reverse == (kind == "PTR"), "PTR is limited to allowlisted reverse zones")
    for protected in zone.get("protected_owners", []):
        protected = fqdn(protected, wildcard=True)
        require(owner != protected and not owner.endswith("." + protected), "Protected DNS owner")
    expected = req["expected"]
    desired = req["desired"]
    require((expected is None) == (op == "create"), "Expected state is required for update/delete only")
    require((desired is None) == (op == "delete"), "Desired state is required for create/update only")
    expected = state(kind, expected, current=True) if expected is not None else None
    desired = state(kind, desired) if desired is not None else None
    for item in (expected, desired):
        if item and "target" in item["data"]:
            require(item["data"]["target"] != owner, "Self-referencing target")
    if op == "update":
        require({k: expected[k] for k in ("ttl", "data")} != desired, "Update makes no change")
    window = req["window"]
    if window is not None:
        shape(window, "start end")
        start, end = timestamp(window["start"]), timestamp(window["end"])
        require(0 < (end-start).total_seconds() <= 86400, "Window must be 24 hours or less")
    require(window is not None or zone.get("require_window") is not True, "Change window is required")
    return {**req, "owner": owner, "expected": expected, "desired": desired}, zone


def check_window(req):
    window = req["window"]
    if window:
        require(timestamp(window["start"]) <= datetime.now(timezone.utc) < timestamp(window["end"]),
                "Outside the approved UTC change window")
