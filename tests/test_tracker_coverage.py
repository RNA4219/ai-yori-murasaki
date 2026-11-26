"""合成の課題と複数スナップショットで型、範囲、不在の根拠を検証する。"""

import unittest

from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.observations import _validate_observation
from ai_yori_murasaki.tracker_drift import build_drift


NOW = "2026-11-21T12:00:00Z"
OLD = "2026-11-20T12:00:00Z"
MAPPING = {"fields": {"scope": "Account", "provider": "Provider", "region": "Region", "status": "State"},
           "status_values": {"Closed": "done"}}
COVERAGE = {"types": ["disk"], "id_patterns": {"disk": ["disk-[0-9]{4}"]}}


def inventory(*resources, account="demo", regions=("north",), when=NOW, **config):
    return {"kind": "inventory", "schema_version": "1.0.0", "method": "fixture", "collected_at": when,
            "scope": {"provider": "sample", "account": account, "regions": list(regions)},
            "resources": [{"id": rid, "type": kind, "name": name, "state": "ok"}
                          for rid, kind, name in resources], **config}


def detections(*signals):
    obs = inventory()
    del obs["resources"]
    return {**obs, "kind": "detections", "detections": [
        {"id": str(i), "resource_id": rid, "rule": "retention", "status": state, "updated_at": NOW,
         **({"resource_type": kind} if kind is not None else {})}
        for i, (rid, kind, state) in enumerate(signals)]}


def compare(title, observations, *, mapping=MAPPING, **overrides):
    props = {"Provider": "sample", "Account": "demo", "Region": "north", **overrides}
    finding = {"finding_id": "case", "title": title, "extensions": {"notion": {"properties": props}}}
    for obs in observations:
        _validate_observation(obs)
    return build_drift("synthetic", [finding], {}, mapping, observations)["items"][0]


class TrackerCoverageTests(unittest.TestCase):
    def test_validation_of_typed_identity_and_optional_fields(self):
        disk = ("shared", "disk", "QuartzDrive")
        bucket = ("shared", "bucket", "AmberVault")
        _validate_observation(inventory(disk, bucket, **COVERAGE))
        _validate_observation(detections(("shared", None, "active")))
        invalid = [inventory(disk, disk), inventory(types="disk"), inventory(types=["disk", "disk"]),
                   inventory(id_patterns=["disk"]), inventory(id_patterns={"disk": "disk-[0-9]+"}),
                   detections(("shared", "", "active"))]
        for pattern in ("disk-(.+)+", "disk-[0-9]{0,5}", "disk-[0-9]{7,2}", "disk-[0-9]{999}",
                        "scope:disk-[0-9]+", "path/disk-[0-9]+"):
            invalid.append(inventory(id_patterns={"disk": [pattern]}))
        for obs in invalid:
            with self.subTest(observation=obs), self.assertRaises(DataError):
                _validate_observation(obs)

    def test_ambiguous_untyped_signal_and_mismatched_type(self):
        assets = inventory(("shared", "disk", "QuartzDrive"), ("shared", "bucket", "AmberVault"))
        rows = [assets, detections(("shared", None, "active"), ("shared", "bucket", "resolved"))]
        disk = compare("QuartzDrive", rows)
        self.assertEqual(disk["reason"], "inferred_only")
        self.assertEqual(disk["matches"][0]["detections"], [])
        bucket = compare("AmberVault", rows)
        self.assertEqual(bucket["classification"], "open_resolved")
        self.assertEqual([d["resource_type"] for d in bucket["matches"][0]["detections"]], ["bucket"])
        self.assertEqual(compare("shared disk", rows)["reason"], "inferred_only")
        wrong = compare("QuartzDrive", [assets, detections(("shared", "queue", "resolved"))])
        self.assertEqual(wrong["reason"], "no_detections")

    def test_disjoint_coverage_and_contradictory_inventories_are_order_independent(self):
        disk = ("disk-1200", "disk", "QuartzDrive")
        present = inventory(disk, **COVERAGE)
        absent = inventory(**COVERAGE)
        outside = inventory(types=["bucket"])
        for a, b, expected in [(present, outside, True), (present, absent, None)]:
            for pair in ([a, b], [b, a]):
                self.assertIs(compare("disk-1200", pair)["matches"][0]["in_inventory"], expected)
        conflict = compare("disk-3300", [absent, inventory(types=["disk"])])
        self.assertIsNone(conflict["matches"][0]["in_inventory"])
        separated = compare("disk-3300", [absent, outside])
        self.assertEqual(separated["classification"], "open_absent")

    def test_history_keeps_resource_types_separate(self):
        old = inventory(("shared", "bucket", "AmberVault"), when=OLD)
        latest = inventory(("shared", "disk", "QuartzDrive"), types=["disk"])
        signal = detections(("shared", "disk", "active"))
        historical = compare("AmberVault", [latest, old, signal])
        self.assertIsNone(historical["matches"][0]["in_inventory"])
        self.assertEqual(historical["reason"], "no_detections")
        self.assertEqual(historical["matches"][0]["detections"], [])
        live = compare("QuartzDrive", [old, signal, latest], State="Closed")
        self.assertEqual(live["classification"], "closed_active")

    def test_unknown_id_requires_unique_scope_kind_and_full_identifier(self):
        current = inventory(**COVERAGE)
        self.assertEqual(compare("対象disk-0198を確認", [current])["classification"], "open_absent")
        variants = [
            ("disk-0198", [current], {"Provider": ""}),
            ("disk-0198", [current], {"Region": "south"}),
            ("disk-0198", [current], {"Account": "demo east"}),
            ("disk-0198", [current, inventory(**COVERAGE, regions=("north", "south"))], {}),
            ("disk-0198", [inventory(types=["disk", "bucket"],
                                     id_patterns={k: ["disk-[0-9]+"] for k in ("disk", "bucket")})], {}),
            ("disk-0198", [current, inventory(("disk-0198", "disk", "ForeignDisk"), account="other")], {}),
            ("urn:sample:other:disk/disk-0198", [current], {}),
            ("neighbor.disk-0198", [current], {}),
            ("disk-01980", [current], {}),
        ]
        for title, obs, props in variants:
            with self.subTest(title=title, props=props, observation_count=len(obs)):
                row = compare(title, obs, **props)
                self.assertFalse(any(m["in_inventory"] is False for m in row["matches"]))

    def test_known_signal_still_needs_covered_type_and_scope(self):
        signal = detections(("disk-0234", "disk", "active"))
        for inv, props in [(inventory(types=["bucket"]), {}), (inventory(**COVERAGE), {"Region": ""}),
                           (inventory(**COVERAGE), {"Provider": ""})]:
            self.assertIsNone(compare("disk-0234", [inv, signal], **props)["matches"][0]["in_inventory"])
        self.assertIs(compare("disk-0234", [inventory(**COVERAGE), signal])["matches"][0]["in_inventory"], False)
        # 一意の既知 ID と旧形式の観測では、記号で挟まれた従来の照合も維持する。
        legacy = [inventory(), detections(("disk-0234", None, "resolved"))]
        for title in ("ID:disk-0234", "/disk-0234", "disk-0234/node-other"):
            self.assertEqual(compare(title, legacy, Provider="", Region="")["classification"], "open_absent")

    def test_unknown_type_or_scope_of_a_second_id_prevents_all_absent(self):
        declared = {"disk": ["disk-[0-9]{4}"], "queue": ["queue-[0-9]{4}"], "bus": ["bus-[0-9]{4}"]}
        for title, kinds, foreign in [
            ("disk-0234 queue-0123", declared, []),
            ("disk-0234 bus-0123", {**declared, "hub": ["bus-[0-9]{4}"]}, []),
            ("disk-0234 disk-0912", declared, [inventory(("disk-0912", "disk", ""), account="other")]),
        ]:
            with self.subTest(title=title):
                row = compare(title, [inventory(types=["disk"], id_patterns=kinds), *foreign])
                self.assertEqual(row["classification"], "uncomparable")
                self.assertCountEqual([match["in_inventory"] for match in row["matches"]], [False, None])

    def test_overlapping_scopes_cannot_promote_legacy_absence_or_detection(self):
        broad = inventory(regions=("east", "north"))
        broad_signal = detections(("disk-0123", None, "active"))
        broad_signal["scope"] = broad["scope"]
        narrow = inventory(("disk-0123", "disk", "NarrowDisk"), **COVERAGE)
        for status in ("Closed", "Open"):
            row = compare("disk-0123", [broad, broad_signal, narrow], State=status)
            self.assertEqual(row["reason"], "inferred_only")
            self.assertFalse(any(m["in_inventory"] is False for m in row["matches"]))
        typed_signal = detections(("disk-0123", "disk", "active"))
        row = compare("disk-0123", [broad, typed_signal], Region="")
        self.assertIsNone(row["matches"][0]["in_inventory"])
        row = compare("disk-0123", [inventory(), typed_signal], Region="")
        self.assertIsNone(row["matches"][0]["in_inventory"])

    def test_legacy_renaming_uses_only_the_latest_identity(self):
        old = inventory(("asset", "storage", "OldDrive"), when=OLD)
        current = inventory(("asset", "volume", "NewDrive"))
        obs = [current, detections(("asset", None, "active")), old]
        for title in ("asset", "NewDrive"):
            row = compare(title, obs, State="Closed")
            self.assertEqual(row["classification"], "closed_active")
            self.assertEqual(row["matches"][0]["type"], "volume")
        self.assertEqual(compare("OldDrive", obs)["reason"], "no_resource")

    def test_exclusion_does_not_disambiguate_a_bare_id(self):
        obs = [inventory(("shared", "disk", "LeftArchive"), ("shared", "volume", "RightArchive")),
               detections(("shared", "disk", "active"))]
        mapping = {**MAPPING, "drift": {"excluded_terms": ["RightArchive"]}}
        row = compare("shared", obs, mapping=mapping, State="Closed")
        self.assertEqual(row["excluded_resource_count"], 1)
        self.assertEqual(row["reason"], "inferred_only")
