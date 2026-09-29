"""report_ecs: legend labels and the cluster-wide ops/sec behind "Peak Ops/Sec".

Labels: the ElastiCache original told MemoryDB-side rows from ECS rows by
dimension name alone (CacheClusterId / ReplicationGroupId existed only on
ElastiCache rows). MemoryDB's cluster dimension is ClusterName, exactly like
ECS's, so only the namespace can say which service a row belongs to.

Ops/sec: the original summed CmdGet/CmdSet, which are Memcached metrics and
never match a Redis or Valkey row, so the chart was always empty and the peak
always 0.
"""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTER_DIR = str(ROOT / "reporter")
if REPORTER_DIR not in sys.path:
    sys.path.insert(0, REPORTER_DIR)

import metric_source  # noqa: E402
from report_ecs import cluster_ops_per_sec, parse_dimensions  # noqa: E402


class ParseDimensionsTests(unittest.TestCase):
    def test_memorydb_node_row_is_labelled_by_node_id(self):
        dims = metric_source.node_dim("memdb-lab-12345678", "memdb-lab-12345678-0001-001")

        self.assertEqual(
            parse_dimensions(dims, metric_source.NAMESPACE),
            "Node: memdb-lab-12345678-0001-001",
        )

    def test_memorydb_cluster_row_is_labelled_as_the_cluster(self):
        dims = metric_source.cluster_dim("memdb-lab-12345678")

        self.assertEqual(parse_dimensions(dims, metric_source.NAMESPACE), "Cluster: memdb-lab-12345678")

    def test_nodes_of_one_cluster_get_distinct_labels(self):
        labels = {
            parse_dimensions(metric_source.node_dim("c", node), metric_source.NAMESPACE)
            for node in ("c-0001-001", "c-0001-002", "c-0002-001")
        }

        self.assertEqual(len(labels), 3)

    def test_ecs_rows_are_not_mistaken_for_memorydb_nodes(self):
        self.assertEqual(
            parse_dimensions("ClusterName=c-loadgen;ServiceName=c-loadgen", "AWS/ECS"),
            "Service: c-loadgen",
        )
        self.assertEqual(
            parse_dimensions("ClusterName=c-loadgen", "ECS/ContainerInsights"),
            "ECS Cluster: c-loadgen",
        )


class ClusterOpsPerSecTests(unittest.TestCase):
    def _frame(self, rows):
        try:
            import pandas as pd
        except ModuleNotFoundError as exc:
            self.skipTest(f"{exc.name} is not installed in this environment")
        frame = pd.DataFrame([
            {
                "Timestamp": pd.Timestamp("2026-05-01T00:00:00"),
                "Namespace": namespace,
                "MetricName": metric_name,
                "Stat": "Sum",
                "Value": value,
                "Dimensions": dimensions,
            }
            for namespace, metric_name, value, dimensions in rows
        ])
        return frame

    def test_aggregate_and_node_rows_are_not_both_counted(self):
        aggregate = metric_source.cluster_dim("cluster-a")
        node = metric_source.node_dim("cluster-a", "cluster-a-0001-001")
        df = self._frame([
            (metric_source.NAMESPACE, "GetTypeCmds", 600, aggregate),
            (metric_source.NAMESPACE, "GetTypeCmds", 600, node),
            (metric_source.NAMESPACE, "SetTypeCmds", 60, aggregate),
            (metric_source.NAMESPACE, "SetTypeCmds", 60, node),
        ])

        ops = cluster_ops_per_sec(df, "cluster-a")

        # (600 gets + 60 sets) over a 60-second period, counted once.
        self.assertEqual(ops.tolist(), [11.0])

    def test_node_rows_are_summed_when_no_aggregate_is_published(self):
        df = self._frame([
            (metric_source.NAMESPACE, "GetTypeCmds", 300, metric_source.node_dim("cluster-a", "cluster-a-0001-001")),
            (metric_source.NAMESPACE, "GetTypeCmds", 300, metric_source.node_dim("cluster-a", "cluster-a-0002-001")),
            (metric_source.NAMESPACE, "SetTypeCmds", 60, metric_source.node_dim("cluster-a", "cluster-a-0001-001")),
        ])

        ops = cluster_ops_per_sec(df, "cluster-a")

        self.assertEqual(ops.tolist(), [11.0])

    def test_memcached_counters_and_other_namespaces_are_ignored(self):
        aggregate = metric_source.cluster_dim("cluster-a")
        df = self._frame([
            (metric_source.NAMESPACE, "CmdGet", 600, aggregate),
            (metric_source.NAMESPACE, "CmdSet", 60, aggregate),
            ("AWS/ECS", "GetTypeCmds", 600, "ClusterName=cluster-a"),
        ])

        ops = cluster_ops_per_sec(df, "cluster-a")

        self.assertTrue(ops.empty)


if __name__ == "__main__":
    unittest.main()
