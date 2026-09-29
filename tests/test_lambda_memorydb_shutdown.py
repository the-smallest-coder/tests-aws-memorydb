"""WP4: the shutdown Lambdas must speak MemoryDB's API, not ElastiCache's.

The source project deleted a replication group and described replication
groups. MemoryDB has neither concept: a cluster is deleted by name, replicas
go with it (there is no RetainPrimaryCluster), and the not-found signal is
ClusterNotFoundFault. Getting any of this wrong fails at teardown time, which
is exactly when a cluster is left running and billing.
"""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LAMBDA_DIR = ROOT / "lambda"


class ClusterNotFoundFault(Exception):
    """Stands in for the generated botocore memorydb exception class."""


class _FakeMemoryDb:
    def __init__(self, describe_result=None, describe_raises=None):
        self.exceptions = types.SimpleNamespace(
            ClusterNotFoundFault=ClusterNotFoundFault
        )
        self.delete_calls = []
        self.describe_calls = []
        self._describe_result = describe_result
        self._describe_raises = describe_raises
        self.delete_raises = None

    def delete_cluster(self, **kwargs):
        self.delete_calls.append(kwargs)
        if self.delete_raises:
            raise self.delete_raises
        return {"Cluster": {"Name": kwargs.get("ClusterName"), "Status": "deleting"}}

    def describe_clusters(self, **kwargs):
        self.describe_calls.append(kwargs)
        if self._describe_raises:
            raise self._describe_raises
        return self._describe_result


class _FakeEcs:
    def __init__(self):
        self.update_calls = []

    def update_service(self, **kwargs):
        self.update_calls.append(kwargs)
        return {}

    def describe_services(self, cluster, services):
        return {"services": [{"runningCount": 0, "desiredCount": 0, "status": "ACTIVE"}]}


def _load(module_name):
    """Import a lambda module by path with boto3 stubbed out at import time."""
    fake_boto3 = types.SimpleNamespace(client=lambda *a, **k: object())
    spec = importlib.util.spec_from_file_location(
        module_name + "_wp4_test_subject", LAMBDA_DIR / (module_name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"boto3": fake_boto3}):
        spec.loader.exec_module(module)
    return module


BASE_ENV = {
    "CLUSTER_ID": "memdb-lab-12345678",
    "ECS_CLUSTER": "memdb-lab-12345678-loadgen",
    "ECS_SERVICE": "memdb-lab-12345678-loadgen",
    "MEMORYDB_CLUSTER_NAME": "memdb-lab-12345678",
}


class ShutdownLambdaTests(unittest.TestCase):
    def _run(self, env_extra=None, delete_raises=None):
        module = _load("shutdown")
        module.ecs = _FakeEcs()
        module.memorydb = _FakeMemoryDb()
        module.memorydb.delete_raises = delete_raises
        env = dict(BASE_ENV)
        env.update(env_extra or {})
        with patch.dict("os.environ", env, clear=True):
            result = module.handler({}, None)
        return module, result

    def test_delete_targets_the_cluster_by_name(self):
        module, _ = self._run()

        self.assertEqual(
            module.memorydb.delete_calls,
            [{"ClusterName": "memdb-lab-12345678"}],
        )

    def test_delete_does_not_send_elasticache_only_parameters(self):
        """RetainPrimaryCluster and ReplicationGroupId are not in the MemoryDB
        delete_cluster signature; sending either is a hard API error at the one
        moment teardown must not fail.
        """
        module, _ = self._run()

        sent = module.memorydb.delete_calls[0]
        self.assertNotIn("RetainPrimaryCluster", sent)
        self.assertNotIn("ReplicationGroupId", sent)
        self.assertNotIn("FinalSnapshotIdentifier", sent)

    def test_final_snapshot_uses_memorydb_parameter_name(self):
        module, _ = self._run(
            {"MEMORYDB_FINAL_SNAPSHOT_NAME": "run-20260922-final"}
        )

        self.assertEqual(
            module.memorydb.delete_calls[0],
            {
                "ClusterName": "memdb-lab-12345678",
                "FinalSnapshotName": "run-20260922-final",
            },
        )

    def test_successful_delete_is_reported_under_a_memorydb_key(self):
        _, result = self._run()

        body = json.loads(result["body"])
        self.assertTrue(body["memorydb_stopped"])
        self.assertNotIn("elasticache_stopped", body)

    def test_delete_failure_is_recorded_rather_than_raised(self):
        """ECS is already scaled to zero by this point. Raising here would lose
        that fact and the error detail; the inherited contract is to record the
        exception text and still return 200.
        """
        _, result = self._run(delete_raises=RuntimeError("throttled"))

        body = json.loads(result["body"])
        self.assertEqual(result["statusCode"], 200)
        self.assertIn("throttled", body["memorydb_stopped"])


class VerifyShutdownProbeTests(unittest.TestCase):
    def _probe(self, describe_result=None, describe_raises=None):
        module = _load("verify_shutdown")
        module.memorydb = _FakeMemoryDb(
            describe_result=describe_result, describe_raises=describe_raises
        )
        return module

    def test_describe_queries_by_cluster_name(self):
        module = self._probe({"Clusters": [{"Status": "available"}]})

        running, detail = module._memorydb_running("memdb-lab-12345678")

        self.assertEqual(
            module.memorydb.describe_calls,
            [{"ClusterName": "memdb-lab-12345678"}],
        )
        self.assertTrue(running)
        self.assertEqual(detail, "status=available")

    def test_cluster_not_found_means_teardown_succeeded(self):
        module = self._probe(describe_raises=ClusterNotFoundFault("gone"))

        running, detail = module._memorydb_running("memdb-lab-12345678")

        self.assertFalse(running)
        self.assertEqual(detail, "not_found")

    def test_empty_cluster_list_means_teardown_succeeded(self):
        module = self._probe({"Clusters": []})

        running, detail = module._memorydb_running("memdb-lab-12345678")

        self.assertFalse(running)
        self.assertEqual(detail, "not_found")

    def test_cluster_already_deleting_does_not_block_the_report(self):
        """verify_shutdown fires once, 15 minutes after the shutdown Lambda.
        A delete that is accepted but still in progress must not read as
        "still running", or the exporter never launches and the run gets no
        report.
        """
        module = self._probe({"Clusters": [{"Status": "deleting"}]})

        running, detail = module._memorydb_running("memdb-lab-12345678")

        self.assertFalse(running)
        self.assertEqual(detail, "status=deleting")

    def test_unknown_describe_failure_assumes_still_running(self):
        """Fail-safe direction: an unreadable cluster must not be reported as
        torn down, or the verify Lambda green-lights a cluster that is still
        billing.
        """
        module = self._probe(describe_raises=RuntimeError("no credentials"))

        running, detail = module._memorydb_running("memdb-lab-12345678")

        self.assertTrue(running)
        self.assertIn("describe_failed", detail)


class LambdaSourceHygieneTests(unittest.TestCase):
    def test_no_elasticache_api_surface_remains_in_lambda_sources(self):
        for path in sorted(LAMBDA_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for banned in (
                "elasticache",
                "ElastiCache",
                "ReplicationGroup",
                "replication_group",
            ):
                self.assertNotIn(
                    banned,
                    text,
                    path.name + " still references " + banned,
                )

    def test_clients_are_created_for_memorydb(self):
        for name in ("shutdown", "verify_shutdown"):
            text = (LAMBDA_DIR / (name + ".py")).read_text(encoding="utf-8")
            self.assertIn('boto3.client("memorydb")', text, name)


if __name__ == "__main__":
    unittest.main()
