import copy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dnsgitops.engine import PendingChange, apply, authoritative, expected_wire, payload, wire_value
from dnsgitops.model import ROOT, decode, load, validate


class DNS(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.state_dir = scratch.name
        self.zones = load(ROOT / "config/zones.json")
        self.zones["lab-internal"]["enabled"] = True
        self.create = load(ROOT / "examples/create-A.json")
        self.update = load(ROOT / "examples/update-CNAME.json")
        self.delete = load(ROOT / "examples/delete-TXT.json")

    def test_pending_change_blocks_other_tickets_until_verified(self):
        first = PendingChange(self.state_dir, "DNS-123")
        second = PendingChange(self.state_dir, "DNS-124")
        first.begin()
        first.begin()
        with self.assertRaisesRegex(ValueError, "pending reconciliation"):
            second.check()
        first.complete()
        second.begin()
        second.complete()

    def test_all_examples_validate(self):
        for request in (self.create, self.update, self.delete):
            self.assertEqual(validate(request, self.zones)[0], request)

    def test_rejects_bad_inputs(self):
        cases = [
            {"owner": "outside.example.net"}, {"owner": "admin.lab.example.net"},
            {"owner": "*.lab.example.net"}, {"owner": "lab.example.net"},
            {"operation": "delete"}, {"zone_key": "lab-reverse"}, {"type": "NS"},
            {"desired": {"ttl": 30, "data": {"addresses": ["192.0.2.44"]}}},
            {"desired": {"ttl": 300, "data": {"addresses": ["2001:db8::44"]}}},
        ]
        for edit in cases:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                validate({**self.create, **edit}, self.zones)
        with self.assertRaises(ValueError):
            decode('{"ttl":300,"ttl":600}')

    def test_authoritative_txt_escapes(self):
        self.assertEqual(wire_value("TXT", r'"a\032b\"c"'), ('a b"c',))

    def test_native_host_payload_disables_reverse(self):
        result = payload("A", "build-api", self.create["desired"], [{"id": 501, "type": "IPv4Address"}])
        self.assertEqual(result["type"], "HostRecord")
        self.assertFalse(result["reverseRecord"])
        self.assertEqual(result["addresses"], [{"id": 501, "type": "IPv4Address"}])

    @patch("dnsgitops.engine.dig")
    def test_cname_at_owner_falls_back_to_parent_for_boundary(self, dig):
        zone = {**self.zones["lab-internal"], "authorities": ["192.0.2.53"]}
        request = self.update
        cname = [(request["owner"], 300, "CNAME", "build02.lab.example.net.")]
        dig.side_effect = [cname, [(zone["fqdn"], 300, "SOA", "ns.example.net.")], cname]
        self.assertEqual(authoritative(zone, request["owner"], "CNAME"),
                         [expected_wire("CNAME", request["desired"])])

    @patch("dnsgitops.engine.authoritative")
    @patch("dnsgitops.engine.Bam")
    def test_create_rechecks_and_quick_deploys(self, bam_class, authoritative_dns):
        request, zone = validate(self.create, self.zones)
        owner = request["owner"]
        bam = bam_class.return_value.__enter__.return_value
        record = {"id": 700, "absoluteName": owner, "type": "HostRecord",
                  "reverseRecord": False, "ttl": 300, "addresses": [{"address": "192.0.2.44"}]}
        bam.records.side_effect = [[], [record]]
        bam.bam.side_effect = [
            {"absoluteName": zone["fqdn"], "view": {"id": zone["view_id"]}, "configuration": {"id": 1}},
            {"data": [{"id": 501, "address": "192.0.2.44", "type": "IPv4Address", "configuration": {"id": 1}}]},
            None, record, None,
        ]
        after = expected_wire("A", request["desired"])
        authoritative_dns.side_effect = [[[], []], [after, after]]
        self.assertIn("Verified", apply(request, zone, PendingChange(self.state_dir, request["issue_key"]), retries=1))
        calls = [(call.args[0], call.args[1].split("?")[0]) for call in bam.bam.call_args_list]
        self.assertEqual(calls, [("GET", "/zones/1001"), ("GET", "/addresses"), ("POST", "/zones/1001/resourceRecords"),
                                 ("GET", "/resourceRecords/700"), ("POST", "/zones/1001/deployments")])

    @patch("dnsgitops.engine.authoritative")
    @patch("dnsgitops.engine.Bam")
    def test_drift_blocks_delete(self, bam_class, authoritative_dns):
        request, zone = validate(self.delete, self.zones)
        bam = bam_class.return_value.__enter__.return_value
        record = {"id": 3103, "absoluteName": request["owner"], "type": "TXTRecord",
                  "ttl": 300, "text": "unexpected", "_links": {"up": {"href": "/api/v2/zones/1001"}}}
        bam.records.return_value = [record]
        bam.bam.side_effect = [{"absoluteName": zone["fqdn"], "view": {"id": zone["view_id"]}}, record]
        with self.assertRaisesRegex(ValueError, "differs"):
            apply(request, zone, PendingChange(self.state_dir, request["issue_key"]))
        authoritative_dns.assert_not_called()
        self.assertEqual(bam.bam.call_count, 2)

    @patch("dnsgitops.engine.authoritative")
    @patch("dnsgitops.engine.Bam")
    def test_delete_recovery_republishes_without_second_delete(self, bam_class, authoritative_dns):
        request, zone = validate(self.delete, self.zones)
        bam = bam_class.return_value.__enter__.return_value
        bam.records.return_value = []
        bam.bam.side_effect = [{"absoluteName": zone["fqdn"], "view": {"id": zone["view_id"]}}, None]
        before = expected_wire("TXT", request["expected"])
        authoritative_dns.side_effect = [[before, []], [[], []]]
        self.assertIn("Verified", apply(request, zone, PendingChange(self.state_dir, request["issue_key"]), retries=1))
        self.assertEqual([call.args[:2] for call in bam.bam.call_args_list],
                         [("GET", "/zones/1001"), ("POST", "/zones/1001/deployments")])

    @patch("dnsgitops.engine.authoritative")
    @patch("dnsgitops.engine.Bam")
    def test_update_uses_put_and_keeps_metadata(self, bam_class, authoritative_dns):
        request, zone = validate(self.update, self.zones)
        bam = bam_class.return_value.__enter__.return_value
        record = {"id": 3100, "type": "AliasRecord", "name": "build",
                  "absoluteName": request["owner"], "ttl": 600,
                  "linkedRecord": {"id": 3101, "absoluteName": "build01.lab.example.net"},
                  "userDefinedFields": {"team": "Build"},
                  "_links": {"up": {"href": "/api/v2/zones/1001"}}}
        linked = {"id": 3102, "type": "HostRecord", "absoluteName": "build02.lab.example.net",
                  "_links": {"up": {"href": "/api/v2/zones/1001"}}}
        updated = copy.deepcopy(record)
        updated.update(ttl=300, linkedRecord=linked)
        bam.records.side_effect = [[record], [updated]]
        live_zone = {"absoluteName": zone["fqdn"], "view": {"id": zone["view_id"]}}
        bam.bam.side_effect = [live_zone, record, linked, live_zone, None, updated, None]
        before = expected_wire("CNAME", request["expected"])
        after = expected_wire("CNAME", request["desired"])
        authoritative_dns.side_effect = [[before, before], [after, after]]
        self.assertIn("Verified", apply(request, zone, PendingChange(self.state_dir, request["issue_key"]), retries=1))
        method, path, body = bam.bam.call_args_list[4].args
        self.assertEqual((method, path), ("PUT", "/resourceRecords/3100"))
        self.assertEqual(body["userDefinedFields"], {"team": "Build"})
        self.assertEqual(body["linkedRecord"], {"id": 3102, "type": "HostRecord"})
