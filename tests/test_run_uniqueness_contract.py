import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunUniquenessContractTests(unittest.TestCase):
    def test_lambda_functions_wait_for_their_managed_log_groups(self):
        shutdown_tf = (ROOT / "lambda_shutdown.tf").read_text(encoding="utf-8")

        self.assertIn(
            "depends_on = [aws_cloudwatch_log_group.lambda_shutdown]",
            shutdown_tf,
        )
        self.assertIn(
            "depends_on = [aws_cloudwatch_log_group.lambda_shutdown_scheduler]",
            shutdown_tf,
        )
        self.assertIn(
            "depends_on = [aws_cloudwatch_log_group.lambda_shutdown_verify]",
            shutdown_tf,
        )

    def test_reporter_task_deploys_loadgen_analysis_module(self):
        reporter_tf = (ROOT / "reporter.tf").read_text(encoding="utf-8")

        self.assertGreaterEqual(reporter_tf.count('"loadgen_analysis.py"'), 2)

    def test_run_id_full_uses_24_hour_timestamp(self):
        main_tf = (ROOT / "main.tf").read_text(encoding="utf-8")
        match = re.search(r'run_id_full\s*=\s*formatdate\("([^"]+)"', main_tf)
        self.assertIsNotNone(match, "run_id_full formatdate not found in main.tf")
        self.assertEqual(match.group(1), "YYYYMMDDHHmmss")

    def test_cluster_id_and_run_folder_reference_discriminator_suffix(self):
        main_tf = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertRegex(main_tf, r'cluster_id\s*=.*run_disc_suffix')
        self.assertRegex(main_tf, r'run_folder\s*=.*run_disc_suffix')

    def test_memorydb_cluster_has_40_char_precondition(self):
        main_tf = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertIn("precondition", main_tf)
        self.assertIn("40-character", main_tf)

    def test_variable_and_outputs_define_run_id_discriminator(self):
        variables_tf = (ROOT / "variables.tf").read_text(encoding="utf-8")
        outputs_tf = (ROOT / "outputs.tf").read_text(encoding="utf-8")
        self.assertIn('variable "run_id_discriminator"', variables_tf)
        self.assertRegex(variables_tf, r'\[a-z0-9\]\{0,8\}')
        self.assertIn('output "run_id_discriminator"', outputs_tf)

    def test_cluster_details_records_run_id_discriminator(self):
        node_details_tf = (ROOT / "node_details.tf").read_text(encoding="utf-8")
        self.assertIn("run_id_discriminator = var.run_id_discriminator", node_details_tf)

    def test_cluster_details_records_observed_availability_zones(self):
        """The source project pinned the node AZ so repeat runs of one config
        stayed comparable, and recorded only an AZ it had actually requested.
        MemoryDB has no AZ-placement argument at all, so the reproducibility
        guarantee is replaced by an observability one: placement is read back
        off the cluster's own shards attribute after the fact.

        The invariant that survives is the same one -- cluster_details.json
        must never report an AZ that was not the real one. Reading it from the
        resource makes that true by construction, so what has to be guarded is
        that nobody reintroduces a *requested* or inferred AZ (e.g. deriving
        it from var.subnet_ids) as if it were the applied one.
        """
        node_details_tf = (ROOT / "node_details.tf").read_text(encoding="utf-8")
        variables_tf = (ROOT / "variables.tf").read_text(encoding="utf-8")
        main_tf = (ROOT / "main.tf").read_text(encoding="utf-8")

        # Placement is read from the cluster resource, per node.
        self.assertIn("aws_memorydb_cluster.main.shards", node_details_tf)
        self.assertRegex(
            node_details_tf,
            r"availability_zone\s*=\s*node\.availability_zone",
        )

        # Nothing requests an AZ, because nothing can.
        self.assertNotIn("availability_zone", variables_tf)
        self.assertNotIn("preferred_cache_cluster_azs", main_tf)
        self.assertNotIn("pinned_az", main_tf)

        # And nothing infers one from the subnets it was handed.
        self.assertNotRegex(
            node_details_tf,
            r"availability_zone\s*=\s*var\.subnet_ids",
        )

    def test_no_data_source_reads_back_the_cluster_shutdown_deletes(self):
        """``data`` blocks are re-evaluated on every plan, including destroy.
        By the time ``terraform destroy`` runs, the shutdown Lambda has
        already deleted the MemoryDB cluster out-of-band, so a
        ``data "aws_memorydb_*"`` block here would fail to read a cluster
        that no longer exists and break destroy.

        Placement still gets recorded, but through the managed resource's own
        attributes (aws_memorydb_cluster.main.shards), which come from state
        at apply time -- not through a fresh read of the live cluster.
        """
        node_details_tf = (ROOT / "node_details.tf").read_text(encoding="utf-8")
        main_tf = (ROOT / "main.tf").read_text(encoding="utf-8")

        for tf in (node_details_tf, main_tf):
            self.assertNotIn('data "aws_memorydb_', tf)
            self.assertNotIn('data "aws_elasticache_', tf)

    def test_reporter_scripts_are_scoped_by_cluster_id(self):
        reporter_tf = (ROOT / "reporter.tf").read_text(encoding="utf-8")
        self.assertIn('reporter_scripts_prefix = "scripts/${local.cluster_id}/"', reporter_tf)
        self.assertIn('key    = "${local.reporter_scripts_prefix}${each.value}"', reporter_tf)
        self.assertRegex(
            reporter_tf,
            r'download_file\(bucket,\s*f"\$\{local\.reporter_scripts_prefix\}\{mod\}"',
        )
        self.assertNotIn('"scripts/${each.value}"', reporter_tf)
        self.assertNotIn('f"scripts/{mod}"', reporter_tf)


if __name__ == "__main__":
    unittest.main()
