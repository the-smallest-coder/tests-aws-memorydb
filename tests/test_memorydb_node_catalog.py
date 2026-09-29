"""WP2: the node catalog, the burstable prefix tuple, and failing loudly.

The inherited catalog was ElastiCache's, and reusing it here would have been
invisible: the families overlap by name, so `db.r6g.large` looks like it ought
to take `cache.r6g.large`'s byte figure. It must not. MemoryDB does not sell
most of those families at all, and reserves memory differently in the ones it
shares, so a carried-over number mis-sizes memtier_key_maximum on every
auto-sized run and produces a wrong report that looks right.

These tests do not require the catalog to be populated -- that is WP0 work.
They require that whatever ends up in it is MemoryDB's, and that nothing can
quietly fall back when a node type is missing.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ECS_TF = ROOT / "ecs.tf"
MAIN_TF = ROOT / "main.tf"
NODE_DETAILS_TF = ROOT / "node_details.tf"
VARIABLES_TF = ROOT / "variables.tf"
EXPORTER = ROOT / "reporter" / "exporter.py"

# Families AWS documents as burstable. A type in the catalog whose family is
# listed here must be matched by BURSTABLE_NODE_TYPE_PREFIXES, or its
# CPUCredit* metrics silently drop out of the contract.
KNOWN_BURSTABLE_FAMILIES = ("db.t4g.", "db.t3.", "db.t2.")


def _catalog_body() -> str:
    text = ECS_TF.read_text(encoding="utf-8")
    match = re.search(
        r"_node_memory_bytes\s*=\s*\{(?P<body>.*?)\n\s*\}",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise AssertionError("_node_memory_bytes map not found in ecs.tf")
    return match.group("body")


def _catalog_keys() -> set[str]:
    """Node types actually in the catalog, ignoring commented-out placeholders."""
    live = "\n".join(
        line for line in _catalog_body().splitlines()
        if not line.strip().startswith("#")
    )
    return set(re.findall(r'"([^"]+)"\s*=', live)) | set(
        re.findall(r"^\s*([A-Za-z0-9._]+)\s*=", live, flags=re.MULTILINE)
    )


def _burstable_prefixes() -> tuple[str, ...]:
    text = EXPORTER.read_text(encoding="utf-8")
    match = re.search(r"BURSTABLE_NODE_TYPE_PREFIXES\s*=\s*\((?P<body>[^)]*)\)", text)
    if not match:
        raise AssertionError("BURSTABLE_NODE_TYPE_PREFIXES not found in exporter.py")
    return tuple(re.findall(r'"([^"]+)"', match.group("body")))


class NodeCatalogTests(unittest.TestCase):
    def test_no_elasticache_node_types_survive_in_the_catalog(self):
        """Not one cache.* entry, including in comments: a commented-out
        ElastiCache figure is an invitation to uncomment it.
        """
        self.assertNotIn(
            "cache.",
            _catalog_body(),
            "ecs.tf _node_memory_bytes still mentions a cache.* node type",
        )

    def test_every_catalog_entry_is_a_memorydb_node_type(self):
        for node_type in _catalog_keys():
            self.assertTrue(
                node_type.startswith("db."),
                f"{node_type} is not a MemoryDB (db.*) node type",
            )

    def test_every_burstable_family_in_the_catalog_matches_the_prefix_tuple(self):
        """The source project carried
        ("cache.t2.", "cache.t3.", "cache.t4g."), which matches nothing against
        db.t4g.* -- dropping CPUCreditBalance and CPUCreditUsage from the
        contract with no error and no warning. A non-match must fail here, not
        shrug at runtime.
        """
        prefixes = _burstable_prefixes()
        self.assertTrue(prefixes, "BURSTABLE_NODE_TYPE_PREFIXES is empty")

        for prefix in prefixes:
            self.assertTrue(
                prefix.startswith("db."),
                f"burstable prefix {prefix!r} is not a MemoryDB prefix",
            )

        for node_type in _catalog_keys():
            if not node_type.startswith(KNOWN_BURSTABLE_FAMILIES):
                continue
            self.assertTrue(
                node_type.startswith(prefixes),
                f"{node_type} is burstable but matches no entry in "
                f"BURSTABLE_NODE_TYPE_PREFIXES {prefixes}",
            )

    def test_default_node_type_is_in_the_catalog_once_the_catalog_exists(self):
        """Vacuous while WP0 is open. The moment the catalog is populated, a
        default that is not in it must fail here rather than at apply time.
        """
        catalog = _catalog_keys()
        if not catalog:
            self.skipTest("node catalog is empty pending WP0")

        match = re.search(
            r'variable\s+"node_type"\s*\{.*?default\s*=\s*"([^"]+)"',
            VARIABLES_TF.read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "node_type default not found in variables.tf")
        self.assertIn(match.group(1), catalog)


class FailLoudlyTests(unittest.TestCase):
    def test_unknown_node_types_do_not_fall_back_to_a_default(self):
        ecs_tf = ECS_TF.read_text(encoding="utf-8")

        self.assertIn("_advertised_bytes = local._node_memory_bytes[var.node_type]", ecs_tf)
        self.assertNotIn("lookup(local._node_memory_bytes", ecs_tf)

    def test_cluster_details_indexes_the_catalog_rather_than_defaulting_to_zero(self):
        """node_details.tf used lookup(..., 0), which would have recorded a
        zero-byte node for any type missing from the catalog -- and every
        keyspace and cost figure derived from it would inherit that zero.
        """
        node_details = NODE_DETAILS_TF.read_text(encoding="utf-8")

        self.assertIn("local._node_memory_bytes[var.node_type]", node_details)
        self.assertNotIn("lookup(local._node_memory_bytes", node_details)

    def test_node_type_precondition_names_supported_set(self):
        """A bare 'Invalid index' names neither the bad value nor what is
        valid. The cluster's precondition must run alongside it and spell out
        the actual supported node_type set so the failure is actionable
        without reading ecs.tf.
        """
        main_tf = MAIN_TF.read_text(encoding="utf-8")

        self.assertRegex(
            main_tf,
            r"contains\(keys\(local\._node_memory_bytes\),\s*var\.node_type\)",
        )
        self.assertIn("Supported types:", main_tf)
        self.assertIn("sort(keys(local._node_memory_bytes))", main_tf)

    def test_data_tiering_is_gated_on_the_r6gd_family(self):
        """Data tiering is only valid on r6gd nodes and is fixed at create
        time, so the API rejects it several minutes into an apply. Catch it at
        plan time instead.
        """
        main_tf = MAIN_TF.read_text(encoding="utf-8")

        self.assertIn('startswith(var.node_type, "db.r6gd.")', main_tf)


class PricingLookupTests(unittest.TestCase):
    def test_node_price_is_fetched_live_not_from_a_hardcoded_table(self):
        ecs_tf = ECS_TF.read_text(encoding="utf-8")

        self.assertNotIn("_node_redis_hourly_usd", ecs_tf)
        self.assertIn('data "external" "node_price"', ecs_tf)
        self.assertRegex(ecs_tf, r"node_type\s*=\s*var\.node_type")
        self.assertRegex(ecs_tf, r"engine_type\s*=\s*var\.engine_type")
        self.assertRegex(ecs_tf, r"aws_region\s*=\s*var\.aws_region")

    def test_price_script_targets_the_memorydb_service_code(self):
        script = (ROOT / "scripts" / "fetch_memorydb_price.sh").read_text(encoding="utf-8")

        self.assertIn("--service-code AmazonMemoryDB", script)
        self.assertNotIn("--service-code AmazonElastiCache", script)

    def test_price_script_does_not_filter_on_cacheengine(self):
        """cacheEngine is an ElastiCache-only Price List attribute. Filtering
        on it against AmazonMemoryDB matches zero SKUs, so every run would
        report its node price as "unavailable" -- a silent loss of the cost
        section rather than an error.
        """
        script = (ROOT / "scripts" / "fetch_memorydb_price.sh").read_text(encoding="utf-8")

        self.assertNotIn("Field=cacheEngine", script)

    def test_price_script_never_fails_terraform_apply_on_lookup_errors(self):
        script = (ROOT / "scripts" / "fetch_memorydb_price.sh").read_text(encoding="utf-8")

        self.assertIn('source: "unavailable"', script)
        self.assertIn("exit 0", script)
        self.assertNotIn("set -e", script)

    def test_price_script_is_invoked_via_explicit_bash(self):
        ecs_tf = ECS_TF.read_text(encoding="utf-8")

        self.assertIn(
            'program = ["bash", "${path.module}/scripts/fetch_memorydb_price.sh"]',
            ecs_tf,
        )

    def test_node_price_lookup_has_an_escape_hatch_for_destroy(self):
        ecs_tf = ECS_TF.read_text(encoding="utf-8")
        variables_tf = VARIABLES_TF.read_text(encoding="utf-8")

        self.assertIn('variable "enable_price_lookup"', variables_tf)
        self.assertRegex(ecs_tf, r"count\s*=\s*var\.enable_price_lookup\s*\?\s*1\s*:\s*0")


class PlacementRecordingTests(unittest.TestCase):
    def test_cluster_details_records_one_availability_zone_per_node(self):
        """MemoryDB has no AZ-placement argument, so reproducibility is
        replaced by observability: placement cannot be requested, but it can
        be recorded and compared between runs.
        """
        node_details = NODE_DETAILS_TF.read_text(encoding="utf-8")

        self.assertIn("aws_memorydb_cluster.main.shards", node_details)
        self.assertIn("availability_zone = node.availability_zone", node_details)

    def test_no_az_pinning_variable_survived_the_port(self):
        variables_tf = VARIABLES_TF.read_text(encoding="utf-8")

        self.assertNotIn("availability_zone", variables_tf)


if __name__ == "__main__":
    unittest.main()
