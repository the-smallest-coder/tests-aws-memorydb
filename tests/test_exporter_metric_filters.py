import csv
import importlib.util
import io
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
REPORTER_DIR = str(ROOT / "reporter")
if REPORTER_DIR not in sys.path:
    sys.path.insert(0, REPORTER_DIR)


def _load_exporter():
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = mock.Mock(side_effect=AssertionError("unexpected boto3 client"))
    fake_report_generator = types.ModuleType("report_generator")
    fake_report_generator.run_uploaded_report = mock.Mock()

    module_name = "exporter_metric_filter_test_subject"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "reporter" / "exporter.py")
    exporter = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            "boto3": fake_boto3,
            "report_generator": fake_report_generator,
        },
    ):
        spec.loader.exec_module(exporter)
    return exporter


class MetricExportFilterTests(unittest.TestCase):
    def test_task_metric_rows_are_enriched_with_availability_zone(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {
                    "Metrics": [{
                        "MetricName": "CpuUtilized",
                        "Dimensions": [
                            {"Name": "ClusterName", "Value": "cluster-a"},
                            {"Name": "TaskId", "Value": "task-a"},
                        ],
                    }]
                }

            def get_metric_statistics(self, **_params):
                return {"Datapoints": [{
                    "Timestamp": datetime(2026, 5, 21, tzinfo=timezone.utc),
                    "Unit": "None",
                    "Average": 245.0,
                }]}

        class FakeS3:
            def put_object(self, **params):
                self.body = params["Body"]

            def get_object(self, **_params):
                content = (
                    '[2026-05-21T00:00:00] [telemetry] '
                    '{"Type":"Task","TaskId":"task-a","AvailabilityZone":"us-east-1f"}\n'
                )
                return {"Body": io.BytesIO(content.encode("utf-8"))}

        original_cloudwatch = exporter.cloudwatch
        original_s3 = exporter.s3
        fake_s3 = FakeS3()
        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = fake_s3
        try:
            with mock.patch("builtins.print"):
                task_metadata = exporter._task_metadata_from_container_insights_object(
                    "bucket", "container-insights.txt"
                )
                exporter.export_metric_sources_to_s3(
                    [{
                        "namespace": "ECS/ContainerInsights",
                        "dimensions": [{"Name": "ClusterName", "Value": "cluster-a"}],
                        "metric_names": ["CpuUtilized"],
                        "statistics": ["Average"],
                    }],
                    "bucket",
                    "metrics.csv",
                    datetime(2026, 5, 21, tzinfo=timezone.utc),
                    datetime(2026, 5, 21, 0, 1, tzinfo=timezone.utc),
                    task_metadata=task_metadata,
                )
        finally:
            exporter.cloudwatch = original_cloudwatch
            exporter.s3 = original_s3

        self.assertIn(
            "AvailabilityZone=us-east-1f;ClusterName=cluster-a;TaskId=task-a",
            fake_s3.body,
        )
        self.assertEqual(task_metadata, {"task-a": {"AvailabilityZone": "us-east-1f"}})

    def test_metric_source_export_skips_unrequested_discovered_metrics(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def __init__(self):
                self.statistics_requests = []

            def list_metrics(self, **_params):
                return {
                    "Metrics": [
                        {
                            "MetricName": "WantedMetric",
                            "Dimensions": [
                                {"Name": "ClusterName", "Value": "test-cluster"},
                                {"Name": "TaskId", "Value": "task-a"},
                            ],
                        },
                        {
                            "MetricName": "UnrelatedMetric",
                            "Dimensions": [
                                {"Name": "ClusterName", "Value": "test-cluster"},
                                {"Name": "TaskId", "Value": "task-a"},
                            ],
                        },
                    ]
                }

            def get_metric_statistics(self, **params):
                self.statistics_requests.append(params)
                return {"Datapoints": []}

        class FakeS3:
            def put_object(self, **_params):
                return {}

        fake_cloudwatch = FakeCloudWatch()
        original_cloudwatch = exporter.cloudwatch
        original_s3 = exporter.s3
        exporter.cloudwatch = fake_cloudwatch
        exporter.s3 = FakeS3()
        try:
            with mock.patch("builtins.print"):
                exporter.export_metric_sources_to_s3(
                    [
                        {
                            "namespace": "AWS/Test",
                            "dimensions": [{"Name": "ClusterName", "Value": "test-cluster"}],
                            "metric_names": ["WantedMetric"],
                        }
                    ],
                    "bucket",
                    "metrics.csv",
                    datetime(2026, 5, 21, tzinfo=timezone.utc),
                    datetime(2026, 5, 21, 0, 1, tzinfo=timezone.utc),
                )
        finally:
            exporter.cloudwatch = original_cloudwatch
            exporter.s3 = original_s3

        self.assertEqual(
            {request["MetricName"] for request in fake_cloudwatch.statistics_requests},
            {"WantedMetric"},
        )
        self.assertEqual(len(fake_cloudwatch.statistics_requests), 2)

    def test_ecs_client_latency_exports_percentile_rows(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {
                    "Metrics": [
                        {
                            "MetricName": "ClientLatency",
                            "Dimensions": [
                                {"Name": "ClusterName", "Value": "cluster-a"},
                                {"Name": "ServiceName", "Value": "service-a"},
                                {"Name": "TaskId", "Value": "task-a"},
                            ],
                        }
                    ]
                }

            def get_metric_statistics(self, **params):
                self.request = params
                return {
                    "Datapoints": [
                        {
                            "Timestamp": datetime(2026, 5, 21, tzinfo=timezone.utc),
                            "Unit": "Milliseconds",
                            "ExtendedStatistics": {"p50": 1.5, "p99": 9.9, "p99.9": 19.9},
                        }
                    ]
                }

        class FakeS3:
            def put_object(self, **params):
                self.body = params["Body"]

        fake_cloudwatch = FakeCloudWatch()
        fake_s3 = FakeS3()
        original_cloudwatch = exporter.cloudwatch
        original_s3 = exporter.s3
        exporter.cloudwatch = fake_cloudwatch
        exporter.s3 = fake_s3
        try:
            with mock.patch("builtins.print") as fake_print:
                exporter.export_metric_sources_to_s3(
                    [
                        {
                            "namespace": exporter.ECS_CLIENT_LATENCY_METRIC["namespace"],
                            "dimensions": [
                                {"Name": "ClusterName", "Value": "cluster-a"},
                                {"Name": "ServiceName", "Value": "service-a"},
                            ],
                            "metric_names": [exporter.ECS_CLIENT_LATENCY_METRIC["metric_name"]],
                            "optional_metric_names": [exporter.ECS_CLIENT_LATENCY_METRIC["metric_name"]],
                            "statistics": [],
                            "extended_statistics": exporter.ECS_CLIENT_LATENCY_METRIC["stats"],
                            "label": "ECS client latency metric discovery",
                        }
                    ],
                    "bucket",
                    "metrics.csv",
                    datetime(2026, 5, 21, tzinfo=timezone.utc),
                    datetime(2026, 5, 21, 0, 1, tzinfo=timezone.utc),
                )
        finally:
            exporter.cloudwatch = original_cloudwatch
            exporter.s3 = original_s3

        self.assertEqual(fake_cloudwatch.request["ExtendedStatistics"], ["p50", "p99", "p99.9"])
        # Regression: statistics: [] means none, not "unspecified" -- a
        # `.get(...) or STATISTICS`-style fallback can't tell an explicit
        # empty list from an absent key and would silently request all 5
        # regular statistics alongside the percentiles.
        self.assertNotIn("Statistics", fake_cloudwatch.request)
        printed = "\n".join(str(call.args[0]) for call in fake_print.call_args_list if call.args)
        self.assertIn("ECS client latency metric discovery", printed)
        self.assertIn("Namespace=MemoryDB/LoadGenerator", printed)
        self.assertIn("MetricName=ClientLatency", printed)
        self.assertIn("RequestedDimensions=ClusterName=cluster-a;ServiceName=service-a", printed)
        self.assertIn("TaskId=task-a", printed)
        csv_text = fake_s3.body
        self.assertIn("ClientLatency,p50,1.5,Milliseconds", csv_text)
        self.assertIn("ClientLatency,p99,9.9,Milliseconds", csv_text)
        self.assertIn("ClientLatency,p99.9,19.9,Milliseconds", csv_text)

    def test_optional_cpu_credit_metrics_are_discovery_only(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def __init__(self, discovered):
                self.discovered = discovered
                self.statistics_requests = []

            def list_metrics(self, **_params):
                return {"Metrics": self.discovered}

            def get_metric_statistics(self, **params):
                self.statistics_requests.append(params)
                return {"Datapoints": []}

        class FakeS3:
            def put_object(self, **_params):
                return {}

        def run(discovered):
            fake_cloudwatch = FakeCloudWatch(discovered)
            original_cloudwatch = exporter.cloudwatch
            original_s3 = exporter.s3
            exporter.cloudwatch = fake_cloudwatch
            exporter.s3 = FakeS3()
            try:
                with mock.patch("builtins.print"):
                    exporter.export_metric_sources_to_s3(
                        [
                            {
                                "namespace": "AWS/MemoryDB",
                                "dimensions": [{"Name": "NodeName", "Value": "cluster-a-001"}],
                                "metric_names": ["EngineCPUUtilization", "CPUCreditBalance", "CPUCreditUsage"],
                                "optional_metric_names": ["CPUCreditBalance", "CPUCreditUsage"],
                            }
                        ],
                        "bucket",
                        "metrics.csv",
                        datetime(2026, 5, 21, tzinfo=timezone.utc),
                        datetime(2026, 5, 21, 0, 1, tzinfo=timezone.utc),
                    )
            finally:
                exporter.cloudwatch = original_cloudwatch
                exporter.s3 = original_s3
            return {request["MetricName"] for request in fake_cloudwatch.statistics_requests}

        absent = run([])
        present = run([
            {
                "MetricName": "CPUCreditBalance",
                "Dimensions": [{"Name": "NodeName", "Value": "cluster-a-001"}],
            }
        ])

        self.assertEqual(absent, {"EngineCPUUtilization"})
        self.assertEqual(present, {"EngineCPUUtilization", "CPUCreditBalance"})


class DiscoveryDrivenExportTests(unittest.TestCase):
    """WP2 change 1: sources with no metric_names export whatever AWS discovers."""

    def test_source_without_metric_names_exports_everything_discovered(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {
                    "Metrics": [
                        {"MetricName": "TrafficManagementActive", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]},
                        {"MetricName": "ErrorCount", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]},
                    ]
                }

            def get_metric_statistics(self, **_params):
                return {"Datapoints": [{
                    "Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc), "Unit": "Count", "Average": 1.0,
                }]}

        class FakeS3:
            def put_object(self, **params):
                self.body = params["Body"]

        fake_s3 = FakeS3()
        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = fake_s3
        with mock.patch("builtins.print"):
            _uri, stats = exporter.export_metric_sources_to_s3(
                [{"namespace": "AWS/MemoryDB", "dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]}],
                "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(set(stats["discovered"]), {"TrafficManagementActive", "ErrorCount"})


class StatisticsEnumTests(unittest.TestCase):
    def test_statistics_is_the_full_five_member_enum_including_samplecount(self):
        exporter = _load_exporter()

        self.assertEqual(len(exporter.STATISTICS), 5)
        self.assertEqual(set(exporter.STATISTICS), {"SampleCount", "Average", "Sum", "Minimum", "Maximum"})


class PercentileFetchTests(unittest.TestCase):
    def test_combined_call_yields_both_a_statistics_row_and_a_percentile_row(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {"Metrics": [{
                    "MetricName": "SuccessfulReadRequestLatency",
                    "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}],
                }]}

            def get_metric_statistics(self, **params):
                self.request = params
                return {"Datapoints": [{
                    "Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc),
                    "Unit": "Microseconds",
                    "Average": 7.5,
                    "ExtendedStatistics": {"p99": 42.0},
                }]}

        class FakeS3:
            def put_object(self, **params):
                self.body = params["Body"]

        fake_cw = FakeCloudWatch()
        fake_s3 = FakeS3()
        exporter.cloudwatch = fake_cw
        exporter.s3 = fake_s3
        with mock.patch("builtins.print"):
            exporter.export_metric_sources_to_s3(
                [{"namespace": "AWS/MemoryDB", "dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]}],
                "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

        self.assertIn("Statistics", fake_cw.request)
        self.assertIn("ExtendedStatistics", fake_cw.request)
        self.assertIn("SuccessfulReadRequestLatency,Average,7.5", fake_s3.body)
        self.assertIn("SuccessfulReadRequestLatency,p99,42.0", fake_s3.body)

    def test_validation_exception_on_combined_call_falls_back_to_two_calls(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def __init__(self):
                self.calls = []

            def list_metrics(self, **_params):
                return {"Metrics": [{
                    "MetricName": "SuccessfulReadRequestLatency",
                    "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}],
                }]}

            def get_metric_statistics(self, **params):
                self.calls.append(params)
                if "Statistics" in params and "ExtendedStatistics" in params:
                    raise Exception(
                        "ValidationException: you must specify either Statistics or "
                        "ExtendedStatistics, but not both"
                    )
                if "Statistics" in params:
                    return {"Datapoints": [{
                        "Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc),
                        "Unit": "Microseconds", "Average": 7.5,
                    }]}
                return {"Datapoints": [{
                    "Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc),
                    "Unit": "Microseconds", "ExtendedStatistics": {"p99": 42.0},
                }]}

        class FakeS3:
            def put_object(self, **params):
                self.body = params["Body"]

        fake_cw = FakeCloudWatch()
        fake_s3 = FakeS3()
        exporter.cloudwatch = fake_cw
        exporter.s3 = fake_s3
        with mock.patch("builtins.print"):
            exporter.export_metric_sources_to_s3(
                [{"namespace": "AWS/MemoryDB", "dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]}],
                "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(fake_cw.calls), 3)  # 1 combined (rejected) + 2 fallback
        self.assertIn("SuccessfulReadRequestLatency,Average,7.5", fake_s3.body)
        self.assertIn("SuccessfulReadRequestLatency,p99,42.0", fake_s3.body)

    def test_missing_extended_statistics_in_response_is_not_an_error(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {"Metrics": [{
                    "MetricName": "Evictions", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}],
                }]}

            def get_metric_statistics(self, **_params):
                # No ExtendedStatistics key: e.g. Evictions can be 0 (negative
                # values are incompatible with percentiles per the CloudWatch
                # service model) -- a normal case, not a fetch failure.
                return {"Datapoints": [{
                    "Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc), "Unit": "Count", "Sum": 0.0,
                }]}

        class FakeS3:
            def put_object(self, **params):
                self.body = params["Body"]

        fake_s3 = FakeS3()
        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = fake_s3
        with mock.patch("builtins.print"):
            _uri, stats = exporter.export_metric_sources_to_s3(
                [{"namespace": "AWS/MemoryDB", "dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]}],
                "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(stats["errors"], [])
        self.assertIn("Evictions,Sum,0.0", fake_s3.body)


class MetricExportManifestTests(unittest.TestCase):
    def test_one_metric_failing_does_not_block_the_others_and_is_recorded(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {"Metrics": [
                    {"MetricName": "Evictions", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]},
                    {"MetricName": "CurrItems", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]},
                ]}

            def get_metric_statistics(self, **params):
                if params["MetricName"] == "Evictions":
                    raise Exception("Throttling: rate exceeded")
                return {"Datapoints": [{
                    "Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc), "Unit": "Count", "Maximum": 5.0,
                }]}

        class FakeS3:
            def put_object(self, **params):
                self.body = params["Body"]

        fake_s3 = FakeS3()
        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = fake_s3
        with mock.patch("builtins.print"):
            _uri, stats = exporter.export_metric_sources_to_s3(
                [{"namespace": "AWS/MemoryDB", "dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]}],
                "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(stats["errors"]), 1)
        self.assertEqual(stats["errors"][0]["metric"], "Evictions")
        self.assertIn("CurrItems,Maximum,5.0", fake_s3.body)

    def test_zero_datapoint_metric_is_not_an_error(self):
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {"Metrics": [{
                    "MetricName": "TrafficManagementActive",
                    "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}],
                }]}

            def get_metric_statistics(self, **_params):
                return {"Datapoints": []}

        class FakeS3:
            def put_object(self, **_params):
                return {}

        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = FakeS3()
        with mock.patch("builtins.print"):
            _uri, stats = exporter.export_metric_sources_to_s3(
                [{"namespace": "AWS/MemoryDB", "dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]}],
                "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(stats["errors"], [])
        self.assertEqual(stats["zero_datapoints"], ["TrafficManagementActive"])

    def test_exported_names_tracks_which_metrics_actually_produced_data(self):
        """WP2 exit criterion 3 ("every discovered metric is either in
        exported or in zero_datapoints, nothing vanishes silently") wasn't
        actually checkable from the manifest: `exported` is a series count,
        not a set of names, so there was no way to tell *which* discovered
        metrics it covers. `exported_names` closes that gap.
        """
        exporter = _load_exporter()

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {"Metrics": [
                    {"MetricName": "CurrItems", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]},
                    {"MetricName": "TrafficManagementActive", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]},
                ]}

            def get_metric_statistics(self, **params):
                if params["MetricName"] == "CurrItems":
                    return {"Datapoints": [{
                        "Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc), "Unit": "Count", "Maximum": 5.0,
                    }]}
                return {"Datapoints": []}

        class FakeS3:
            def put_object(self, **_params):
                return {}

        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = FakeS3()
        with mock.patch("builtins.print"):
            _uri, stats = exporter.export_metric_sources_to_s3(
                [{"namespace": "AWS/MemoryDB", "dimensions": [{"Name": "NodeName", "Value": "cluster-a"}]}],
                "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(stats["exported_names"], ["CurrItems"])
        self.assertEqual(stats["zero_datapoints"], ["TrafficManagementActive"])
        vanished = set(stats["discovered"]) - set(stats["exported_names"]) - set(stats["zero_datapoints"])
        self.assertEqual(vanished, set())


class ActivityValueCheckTests(unittest.TestCase):
    """Never made it into the 15-finding review: the discovery-based
    completeness gate (WP2) only checks that CacheHits/CacheMisses/CurrItems
    were *published*, not that they carry any real signal -- a run could
    discover all three names yet have zero actual reads/writes recorded
    against them. Restored as an informational value_failures list on the
    manifest (see the comment on export_memorydb_metrics_to_s3), never as
    a gate: a genuinely zero-activity run (e.g. TODO.md's write-only fill
    workload) must still produce a report.
    """

    @staticmethod
    def _run(exporter, metrics, datapoints_by_name):
        class FakeMemoryDb:
            def describe_replication_groups(self, **_params):
                raise Exception("not available in this fake")

        class FakeCloudWatch:
            def list_metrics(self, **_params):
                return {"Metrics": metrics}

            def get_metric_statistics(self, **params):
                return {"Datapoints": datapoints_by_name.get(params["MetricName"], [])}

        class FakeS3:
            def put_object(self, **_params):
                return {}

        exporter.memorydb = FakeMemoryDb()
        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = FakeS3()
        with mock.patch("builtins.print"):
            return exporter.export_memorydb_metrics_to_s3(
                "cluster-a", "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )

    def test_empty_core_activity_metrics_are_flagged_but_do_not_fail_the_export(self):
        exporter = _load_exporter()
        metrics = [
            {"MetricName": "Evictions", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a-001"}]},
        ]

        manifest = self._run(exporter, metrics, {
            "Evictions": [{"Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc), "Unit": "Count", "Sum": 3.0}],
        })

        self.assertEqual(
            manifest["value_failures"],
            [
                "KeyspaceHits/KeyspaceMisses have no positive Sum datapoints",
                "CurrItems has no positive Maximum datapoints",
            ],
        )
        # The point of this whole design: a real data-quality signal must
        # never flip status["checks"]["metrics"]["complete"] to False on its
        # own -- only rows_written > 0 (exercised via manifest["rows_written"]
        # here) is allowed to do that.
        self.assertEqual(manifest["rows_written"], 1)

    def test_positive_core_activity_metrics_clear_the_flag(self):
        exporter = _load_exporter()
        metrics = [
            {"MetricName": "KeyspaceHits", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a-001"}]},
            {"MetricName": "KeyspaceMisses", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a-001"}]},
            {"MetricName": "CurrItems", "Dimensions": [{"Name": "NodeName", "Value": "cluster-a-001"}]},
        ]
        ts = datetime(2026, 8, 10, tzinfo=timezone.utc)

        manifest = self._run(exporter, metrics, {
            "KeyspaceHits": [{"Timestamp": ts, "Unit": "Count", "Sum": 100.0}],
            "KeyspaceMisses": [{"Timestamp": ts, "Unit": "Count", "Sum": 20.0}],
            "CurrItems": [{"Timestamp": ts, "Unit": "Count", "Maximum": 50.0}],
        })

        self.assertEqual(manifest["value_failures"], [])


class MemoryDbDiscoveryCoverageTests(unittest.TestCase):
    """Every series CloudWatch publishes for the cluster must land in the CSV.

    The published shapes below are the ones `aws cloudwatch list-metrics`
    returned for a live cluster on 2026-09-29: a ClusterName-only aggregate
    and a ClusterName+NodeName series per node for every metric, plus
    EngineCPUUtilization under ReplicationGroupId (alone, with Role, with
    NodeGroupId) -- which carries no ClusterName at all.
    """

    PUBLISHED = [
        ("EngineCPUUtilization", {"ClusterName": "cluster-a"}),
        ("EngineCPUUtilization", {"ClusterName": "cluster-a", "NodeName": "cluster-a-0001-001"}),
        ("EngineCPUUtilization", {"ReplicationGroupId": "cluster-a"}),
        ("EngineCPUUtilization", {"ReplicationGroupId": "cluster-a", "Role": "Primary"}),
        ("EngineCPUUtilization", {"ReplicationGroupId": "cluster-a", "Role": "Replica"}),
        ("EngineCPUUtilization", {"NodeGroupId": "0001", "ReplicationGroupId": "cluster-a"}),
        ("KeyspaceHits", {"ClusterName": "cluster-a"}),
        ("KeyspaceHits", {"ClusterName": "cluster-a", "NodeName": "cluster-a-0001-001"}),
        # Another cluster in the same account: must not leak into this export.
        ("KeyspaceHits", {"ClusterName": "cluster-b"}),
    ]

    def _export(self):
        exporter = _load_exporter()
        published = [
            {"MetricName": name, "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]}
            for name, dims in self.PUBLISHED
        ]

        class FakeCloudWatch:
            def list_metrics(self, **params):
                # CloudWatch semantics: a series matches when it carries every
                # requested dimension, whatever other dimensions it has.
                wanted = {(d["Name"], d["Value"]) for d in params.get("Dimensions", [])}
                return {"Metrics": [
                    m for m in published
                    if wanted <= {(d["Name"], d["Value"]) for d in m["Dimensions"]}
                ]}

            def get_metric_statistics(self, **_params):
                return {"Datapoints": [
                    {"Timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc), "Unit": "Percent", "Average": 1.0}
                ]}

        class FakeMemoryDb:
            def describe_clusters(self, **_params):
                raise Exception("cluster already deleted")

        uploads = {}

        class FakeS3:
            def put_object(self, **params):
                uploads[params["Key"]] = params["Body"]
                return {}

        exporter.memorydb = FakeMemoryDb()
        exporter.cloudwatch = FakeCloudWatch()
        exporter.s3 = FakeS3()
        with mock.patch("builtins.print"):
            exporter.export_memorydb_metrics_to_s3(
                "cluster-a", "bucket", "metrics.csv",
                datetime(2026, 8, 10, tzinfo=timezone.utc), datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
            )
        rows = list(csv.DictReader(io.StringIO(uploads["metrics.csv"])))
        return {(row["MetricName"], row["Dimensions"]) for row in rows}

    def test_every_published_series_of_the_cluster_is_exported(self):
        exported = self._export()

        expected = {
            (name, ";".join(f"{k}={v}" for k, v in sorted(dims.items())))
            for name, dims in self.PUBLISHED
            if "cluster-a" in dims.values()
        }
        self.assertEqual(exported, expected)

    def test_node_series_are_keyed_by_node_name(self):
        self.assertIn(
            ("KeyspaceHits", "ClusterName=cluster-a;NodeName=cluster-a-0001-001"),
            self._export(),
        )


class ConditionalContractTests(unittest.TestCase):
    """D8: completeness is a predicate over cluster_details.json, not a fixed list."""

    def test_burstable_node_requires_cpu_credit_metrics_non_burstable_does_not(self):
        exporter = _load_exporter()
        discovered = set(exporter.CORE_MEMORYDB_CONTRACT_METRICS)

        non_burstable = exporter._metric_contract_status(discovered, {"memorydb": {"node_type": "db.r6g.large"}})
        burstable = exporter._metric_contract_status(discovered, {"memorydb": {"node_type": "db.t4g.small"}})

        self.assertTrue(non_burstable["complete"])
        self.assertFalse(burstable["complete"])
        self.assertIn("CPUCreditBalance", burstable["missing"])
        self.assertIn("CPUCreditUsage", burstable["missing"])

    def test_replicas_require_the_replication_metric_family(self):
        """MemoryDB is always sharded, so shard count alone says nothing about
        whether replication traffic exists -- only replicas do. A run with
        num_replicas_per_shard = 0 must not be marked incomplete for lacking
        metrics its topology cannot produce.
        """
        exporter = _load_exporter()
        discovered = set(exporter.CORE_MEMORYDB_CONTRACT_METRICS)

        no_replicas = exporter._metric_contract_status(
            discovered, {"memorydb": {"num_shards": 4, "num_replicas_per_shard": 0}}
        )
        with_replicas = exporter._metric_contract_status(
            discovered, {"memorydb": {"num_shards": 1, "num_replicas_per_shard": 1}}
        )

        self.assertTrue(no_replicas["complete"])
        self.assertFalse(with_replicas["complete"])
        self.assertIn("ReplicationLag", with_replicas["missing"])
        self.assertIn("ReplicationDelayedWriteCommands", with_replicas["missing"])

    def test_data_tiering_requires_the_disk_metric_family(self):
        exporter = _load_exporter()
        discovered = set(exporter.CORE_MEMORYDB_CONTRACT_METRICS)

        plain = exporter._metric_contract_status(discovered, {"memorydb": {"data_tiering": False}})
        tiered = exporter._metric_contract_status(discovered, {"memorydb": {"data_tiering": True}})

        self.assertTrue(plain["complete"])
        self.assertFalse(tiered["complete"])
        self.assertIn("BytesReadFromDisk", tiered["missing"])

    def test_server_latency_is_required_only_where_the_engine_publishes_it(self):
        """Successful{Read,Write}RequestLatency exist on Valkey 7.2+ only. A
        healthy Redis OSS run must not be marked incomplete for lacking them,
        and an engine the contract cannot read must keep them required.
        """
        exporter = _load_exporter()
        latency = {"SuccessfulReadRequestLatency", "SuccessfulWriteRequestLatency"}
        discovered = set(exporter.CORE_MEMORYDB_CONTRACT_METRICS) - latency

        redis = exporter._metric_contract_status(
            discovered, {"memorydb": {"engine": "redis", "engine_version_configured": "7.1"}}
        )
        valkey = exporter._metric_contract_status(
            discovered, {"memorydb": {"engine": "valkey", "engine_version_configured": "7.2"}}
        )
        unknown = exporter._metric_contract_status(discovered, {"memorydb": {}})

        self.assertTrue(redis["complete"], redis["missing"])
        self.assertEqual(set(valkey["missing"]), latency)
        self.assertEqual(set(unknown["missing"]), latency)

    def test_core_contract_names_only_metrics_memorydb_actually_publishes(self):
        """A contract listing metrics that cannot exist reports them missing on
        every healthy run, and a permanently-failing contract is one people
        learn to ignore.
        """
        exporter = _load_exporter()
        contract = set(exporter.CORE_MEMORYDB_CONTRACT_METRICS)

        for name in (
            "ErrorCount",
            "SuccessfulReadRequestLatency",
            "SuccessfulWriteRequestLatency",
            "KeyspaceHits",
            "KeyspaceMisses",
            "BytesUsedForMemoryDB",
        ):
            self.assertIn(name, contract)

        for absent in (
            "CacheHits",
            "CacheMisses",
            "CacheHitRate",
            "BytesUsedForCache",
            "GetTypeCmdsLatency",
            "SetTypeCmdsLatency",
            "StringBasedCmdsLatency",
            "TrafficManagementActive",
            "DatabaseMemoryUsageCountedForEvictPercentage",
            "DatabaseCapacityUsageCountedForEvictPercentage",
        ):
            self.assertNotIn(absent, contract, f"{absent} does not exist on MemoryDB")

    def test_missing_successful_read_request_latency_is_reported(self):
        exporter = _load_exporter()
        discovered = set(exporter.CORE_MEMORYDB_CONTRACT_METRICS) - {"SuccessfulReadRequestLatency"}

        status = exporter._metric_contract_status(discovered, {"memorydb": {"node_type": "db.r6g.large"}})

        self.assertFalse(status["complete"])
        self.assertEqual(status["missing"], ["SuccessfulReadRequestLatency"])


class SummaryWp2FieldDedupTests(unittest.TestCase):
    """Tests 27-29: the new WP2 summary fields go through the same D6 dedup."""

    def _build(self, metrics_rows):
        import pandas as pd
        from summary import build_summary

        metrics_df = pd.DataFrame(metrics_rows) if metrics_rows else pd.DataFrame()
        if not metrics_df.empty:
            metrics_df["Timestamp"] = pd.to_datetime(metrics_df["Timestamp"], utc=True).dt.tz_localize(None)
        return build_summary(
            metrics_df, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            extra_stats={}, config={}, cluster_id="cluster-a", time_range="",
        )

    @staticmethod
    def _row(metric_name, stat, value, dimensions):
        return {
            "Timestamp": "2026-08-10T00:00:00Z",
            "Namespace": "AWS/MemoryDB",
            "MetricName": metric_name,
            "Stat": stat,
            "Value": value,
            "Unit": "Count",
            "Dimensions": dimensions,
        }

    def test_error_count_total_is_not_double_counted(self):
        try:
            summary = self._build([
                self._row("ErrorCount", "Sum", 4, "ClusterName=cluster-a"),
                self._row("ErrorCount", "Sum", 4, "ClusterName=cluster-a;NodeName=cluster-a-0001-001"),
            ])
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed in this environment")
            raise

        self.assertEqual(summary["errors"]["error_count_total"], 4)

    def test_server_request_latency_p99_is_not_double_counted_across_dimension_levels(self):
        try:
            summary = self._build([
                self._row("SuccessfulReadRequestLatency", "p99", 16.0, "ClusterName=cluster-a"),
                self._row("SuccessfulReadRequestLatency", "p99", 999.0, "ClusterName=cluster-a;NodeName=cluster-a-0001-001"),
            ])
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed in this environment")
            raise

        # The aggregate row wins over the node-level duplicate (D6); a mean
        # across both would land nowhere near 16.0.
        self.assertEqual(summary["server_request_latency_us"]["read_p99"], 16.0)

    def test_summary_without_wp2_metrics_has_empty_sections_and_no_exception(self):
        try:
            summary = self._build([])
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed in this environment")
            raise

        self.assertEqual(summary["errors"], {})
        self.assertEqual(summary["server_request_latency_us"], {})


class DedupMetricStatValidationTests(unittest.TestCase):
    """Regression (code review, finding 11): _dedup_metric_stat's agg branch
    was `mean() if agg == 'mean' else max()` -- any other value (a typo,
    a future agg='sum') silently returned the peak under an "avg" name in
    server_request_latency_us.*_avg / baseline_usage_*_avg_pct.
    """

    def test_unsupported_agg_raises_instead_of_silently_returning_max(self):
        try:
            import pandas as pd
            from summary import _dedup_metric_stat
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed in this environment")
            raise

        df = pd.DataFrame([{
            "Timestamp": pd.Timestamp("2026-08-10T00:00:00"), "Namespace": "AWS/MemoryDB",
            "MetricName": "SuccessfulReadRequestLatency", "Stat": "Average", "Value": 5.0,
            "Unit": "Microseconds", "Dimensions": "ClusterName=cluster-a",
        }])

        with self.assertRaises(ValueError):
            _dedup_metric_stat(df, "SuccessfulReadRequestLatency", "cluster-a", "Average", agg="avg")


if __name__ == "__main__":
    unittest.main()
