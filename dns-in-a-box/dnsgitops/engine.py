import ipaddress
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from .api import Bam
from .model import LINKED, check_window, fqdn, number, require, state

BAM_TYPES = {"A": "HostRecord", "AAAA": "HostRecord", "CNAME": "AliasRecord",
             "TXT": "TXTRecord", "MX": "MXRecord", "SRV": "SRVRecord",
             "PTR": "GenericRecord", "CAA": "CAARecord"}


def without_id(state_value):
    return {key: state_value[key] for key in ("ttl", "data")} if state_value else None


def payload(kind, relative, desired, reference):
    data = desired["data"]
    result = {"type": BAM_TYPES[kind], "name": relative, "ttl": desired["ttl"]}
    if kind in {"A", "AAAA"}:
        result.update(reverseRecord=False, addresses=reference)
    elif kind in LINKED:
        result["linkedRecord"] = {"id": data["target_id"], "type": reference["type"]}
        result.update({key: data[key] for key in ("priority", "weight", "port") if key in data})
    elif kind == "PTR":
        result.update(recordType="PTR", rdata=data["target"] + ".")
    else:
        result.update(data)
    return result


def snapshot(kind, record):
    if kind in {"A", "AAAA"}:
        require(record.get("reverseRecord") is False, "Host reverse behavior is enabled or unknown")
        data = {"addresses": [item["address"] for item in record["addresses"]]}
    elif kind in LINKED:
        link = record["linkedRecord"]
        data = {"target_id": link["id"], "target": link["absoluteName"]}
        data.update({key: record[key] for key in ("priority", "weight", "port") if key in record})
    elif kind == "PTR":
        data = {"target": record["rdata"]}
    elif kind == "TXT":
        data = {"text": record["text"]}
    else:
        data = {key: record[key] for key in ("flags", "tag", "value")}
    return state(kind, {"id": record["id"], "ttl": record["ttl"], "data": data}, current=True)


def matching(kind, rows):
    # Generic records for a native type can still occupy its RRset.
    candidates = [row for row in rows if row["type"] == BAM_TYPES[kind]
                  and (kind != "PTR" or row.get("recordType") == kind)]
    if kind != "PTR":
        require(not any(row.get("recordType") == kind and row not in candidates for row in rows),
                "Same type exists as a different BAM object type")
    require(len(candidates) <= 1, "Multiple BAM objects at this RRset require manual handling")
    return candidates[0] if candidates else None


def zone_check(bam, zone):
    live = bam.bam("GET", f"/zones/{zone['zone_id']}")
    require(fqdn(live["absoluteName"]) == fqdn(zone["fqdn"])
            and live["view"]["id"] == zone["view_id"], "BAM zone/view identity differs from policy")
    return live


def address_references(bam, kind, desired, live_zone):
    config_id = number(live_zone["configuration"]["id"], 1, 2**63-1, "configuration ID")
    address_type = "IPv4Address" if kind == "A" else "IPv6Address"
    references = []
    for address in desired["data"]["addresses"]:
        query = urlencode({"filter": f"address:eq('{address}') and configuration.id:eq({config_id})",
                           "limit": 2})
        result = bam.bam("GET", f"/addresses?{query}")
        require(len(result["data"]) == 1 and not result.get("_links", {}).get("next"),
                "Use an existing unique IPAM address in the same BAM configuration")
        row = result["data"][0]
        require(row["type"] == address_type and str(ipaddress.ip_address(row["address"])) == address
                and row["configuration"]["id"] == config_id, "BAM address differs from approved target")
        references.append({"id": number(row["id"], 1, 2**63-1, "IPAM address ID"), "type": address_type})
    return references


def linked_check(bam, desired, zone):
    target = desired["data"]
    linked = bam.bam("GET", f"/resourceRecords/{target['target_id']}")
    require(linked["id"] == target["target_id"] and linked["type"] in {"HostRecord", "ExternalHostRecord", "ExternalHost"}
            and fqdn(linked["absoluteName"]) == target["target"], "Linked BAM target differs from request")
    parent = linked["_links"]["up"]["href"]
    match = re.fullmatch(r"/api/v2/zones/([1-9][0-9]*)", parent)
    require(match, "Could not determine the linked target zone")
    target_zone = bam.bam("GET", f"/zones/{match[1]}")
    require(target_zone["view"]["id"] == zone["view_id"], "Linked target is in another BAM view")
    return linked


def dig(server, owner, kind):
    command = ["dig", "@" + server, "-q", owner + ".", "-t", kind, "+tcp", "+norecurse",
               "+time=3", "+tries=1", "+noall", "+comments", "+answer", "+authority"]
    reply = subprocess.run(command, capture_output=True, text=True, timeout=8, check=True).stdout
    status = re.search(r"status: (\w+)", reply)
    flags = re.search(r";; flags: ([^;]+);", reply)
    require(status and status[1] in {"NOERROR", "NXDOMAIN"} and flags and "aa" in flags[1].split(),
            f"Non-authoritative or unsuccessful DNS response from {server}")
    records = []
    for line in reply.splitlines():
        if line and not line.startswith(";"):
            name, ttl, rdclass, record_type, data = line.split(None, 4)
            require(rdclass == "IN", "Unexpected DNS class")
            records.append((name.lower().rstrip("."), int(ttl), record_type, data))
    return records


def authoritative(bam_zone, owner, kind):
    origin = fqdn(bam_zone["fqdn"])
    results = []
    for server in bam_zone["authorities"]:
        label = owner
        while True:
            answer = dig(server, label, "SOA")
            soa = [name for name, _, rrtype, _ in answer if rrtype == "SOA"]
            if soa:
                require(soa == [origin], f"DNS zone boundary differs on {server}")
                break
            # An authoritative server may answer an SOA query at an alias with only its CNAME.
            require(label != origin and any(rrtype == "CNAME" for _, _, rrtype, _ in answer),
                    f"DNS zone boundary could not be confirmed on {server}")
            label = label.split(".", 1)[1]
        answer = dig(server, owner, kind)
        if kind != "CNAME":
            require(not any(name == owner and rrtype == "CNAME" for name, _, rrtype, _ in answer),
                    f"Authoritative DNS owner is already an alias on {server}")
        records = [(ttl, raw) for name, ttl, rrtype, raw in answer if name == owner and rrtype == kind]
        results.append(sorted((ttl, wire_value(kind, raw)) for ttl, raw in records))
    return results


def wire_value(kind, raw):
    if kind in {"A", "AAAA"}:
        return (str(ipaddress.ip_address(raw)),)
    if kind == "TXT":
        # dig emits DNS presentation escapes. Decode escaped bytes and quoted character strings.
        chunks = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
        require(chunks and re.fullmatch(r'\s*(?:"(?:[^"\\]|\\.)*"\s*)+', raw), "Invalid TXT response")
        return tuple(re.sub(r"\\([0-9]{3}|.)",
                            lambda match: chr(int(match[1])) if len(match[1]) == 3 else match[1],
                            chunk) for chunk in chunks)
    if kind in {"CNAME", "PTR"}:
        return (fqdn(raw),)
    if kind in {"MX", "SRV"}:
        *params, target = raw.split()
        return (*map(int, params), fqdn(target))
    flags, tag, value = shlex.split(raw)
    return (int(flags), tag, value)


def expected_wire(kind, item):
    if item is None:
        return []
    ttl, data = item["ttl"], item["data"]
    if kind in {"A", "AAAA"}:
        return sorted((ttl, (address,)) for address in data["addresses"])
    if kind == "TXT":
        return [(ttl, (data["text"],))]
    if kind == "CAA":
        return [(ttl, tuple(data[key] for key in ("flags", "tag", "value")))]
    fields = {"MX": ("priority",), "SRV": ("priority", "weight", "port")}
    return [(ttl, (*(data[key] for key in fields.get(kind, ())), data["target"]))]


class PendingChange:
    """Keep later jobs from publishing another request's staged BAM changes."""

    def __init__(self, directory, key):
        path = Path(directory)
        require(path.is_absolute() and path.is_dir(), "Configure a persistent runner state directory")
        self.file = path / "pending-change"
        self.key = key

    def check(self):
        if self.file.exists():
            require(self.file.read_text(encoding="ascii").strip() == self.key,
                    "A different BlueCat request is pending reconciliation on this runner")

    def begin(self):
        self.check()
        if not self.file.exists():
            with self.file.open("x", encoding="ascii") as stream:
                stream.write(self.key + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.sync_directory()

    def complete(self):
        self.check()
        if self.file.exists():
            self.file.unlink()
            self.sync_directory()

    def sync_directory(self):
        descriptor = os.open(self.file.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def apply(req, zone, pending, retries=24):
    owner, kind, op = (req[key] for key in ("owner", "type", "operation"))
    desired, expected = req["desired"], req["expected"]
    before, after = expected_wire(kind, expected), expected_wire(kind, desired)
    origin = fqdn(zone["fqdn"])
    relative = owner[:-(len(origin)+1)] if owner != origin else ""
    zone_id = zone["zone_id"]
    check_window(req)
    pending.check()
    with Bam() as bam:
        live_zone = zone_check(bam, zone)
        rows = bam.records(zone_id, owner)
        record = matching(kind, rows)
        if desired and kind == "CNAME":
            require(not any(row is not record for row in rows), "CNAME conflicts with another BAM record")
        if desired and kind != "CNAME":
            require(not any(row["type"] == "AliasRecord" or row.get("recordType") == "CNAME"
                            for row in rows), "Owner is already an alias")
        if record:
            number(record["id"], 1, 2**63-1, "record ID")
            record = bam.bam("GET", f"/resourceRecords/{record['id']}")
            require(fqdn(record["absoluteName"], wildcard=True) == owner
                    and record["_links"]["up"]["href"] == f"/api/v2/zones/{zone_id}",
                    "Record does not belong to the intended owner and zone")
        current = snapshot(kind, record) if record else None
        same_id = op == "create" or not current or current["id"] == expected["id"]
        staged = same_id and without_id(current) == desired
        require(staged or current == expected, "Live BAM state differs from approved before/after state")
        observed = authoritative(zone, owner, kind)
        require(all(value == before or (staged and value == after) for value in observed),
                "Authoritative DNS differs from approved before/after state")
        if staged and all(value == after for value in observed):
            pending.complete()
            return "Already verified on all configured authorities"
        check_window(req)
        if not staged:
            reference = None
            if desired and kind in LINKED:
                reference = linked_check(bam, desired, zone)
            if desired and kind in {"A", "AAAA"}:
                reference = address_references(bam, kind, desired, live_zone)
            if op == "delete" or (op == "update" and kind in {"A", "AAAA"}):
                dependencies = bam.bam("GET", f"/resourceRecords/{record['id']}/dependentRecords?limit=1")
                require(dependencies["data"] == [], "Record has dependent BlueCat objects")
            pending.begin()
            if op == "delete":
                bam.bam("DELETE", f"/resourceRecords/{record['id']}")
            elif op == "update":
                update = payload(kind, record["name"], desired, reference)
                bam.bam("PUT", f"/resourceRecords/{record['id']}", {**record, **update})
            else:
                bam.bam("POST", f"/zones/{zone_id}/resourceRecords", payload(kind, relative, desired, reference))
            result = matching(kind, bam.records(zone_id, owner))
            if desired:
                require(result is not None, "BAM write did not return the intended object")
                result = bam.bam("GET", f"/resourceRecords/{result['id']}")
                require(op != "update" or result["id"] == expected["id"], "BAM update changed the object ID")
            require(without_id(snapshot(kind, result) if result else None) == desired,
                    "BAM write did not produce approved state. Review BAM before publishing.")
        pending.begin()
        print("BAM is staged. Requesting BlueCat Quick Deploy.", flush=True)
        bam.bam("POST", f"/zones/{zone_id}/deployments", {"type": "QuickDeployment"})
        for _ in range(retries):
            try:
                if all(value == after for value in authoritative(zone, owner, kind)):
                    pending.complete()
                    return "Verified on all configured authorities"
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            time.sleep(5)
    raise RuntimeError("Verification timed out. Inspect BAM deployment and authoritative DNS before rerun.")
