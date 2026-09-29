"""The single place that knows which CloudWatch namespace and dimensions this
project reads.

Every `metric_filter` call and every dimension string in the reporter routes
through here. The reason is mechanical: the inherited code carried the strings
``AWS/ElastiCache``, ``ReplicationGroupId`` and ``CacheClusterId`` inline in
sixty-five places across eight modules. A rename done by search-and-replace
across that many literals leaves survivors, and a survivor does not raise --
``metric_filter`` on a dimension prefix that matches nothing returns an empty
DataFrame, which renders as an empty panel. The failure looks like "that metric
had no data", not like a bug.

So: no bare dimension literal anywhere else in reporter/. Tests enforce it.
"""

from __future__ import annotations


# --- CloudWatch source -------------------------------------------------------

NAMESPACE = "AWS/MemoryDB"

# Dimension names below were checked against `aws cloudwatch list-metrics` on
# a live cluster (2026-09-29): every AWS/MemoryDB metric is published as a
# ClusterName-only aggregate plus one ClusterName+NodeName series per node.

#: Cluster-level dimension. ElastiCache called this ``ReplicationGroupId``.
CLUSTER_DIMENSION = "ClusterName"

#: Node-level dimension; its value is the full node name
#: (``<cluster>-0001-001``). Not ``NodeId`` -- AWS's own docs example shows
#: NodeId, but CloudWatch publishes NodeName. ElastiCache called this
#: ``CacheClusterId``. Node-level series carry CLUSTER_DIMENSION *and* this one
#: together: on ElastiCache the node dimension was self-sufficient, so
#: inherited code that filters on the node dimension alone under-selects here.
NODE_DIMENSION = "NodeName"

#: MemoryDB additionally publishes EngineCPUUtilization in ElastiCache-style
#: dimensions -- ReplicationGroupId (= the cluster name) alone, with Role
#: (Primary / Replica), and with NodeGroupId (the shard). None of those series
#: carry ClusterName, so discovery by CLUSTER_DIMENSION never returns them and
#: the exporter lists them separately by this dimension.
REPLICATION_GROUP_DIMENSION = "ReplicationGroupId"

#: Only present on data-tiering (r6gd) clusters. Values: ``Memory`` / ``SSD``.
TIER_DIMENSION = "Tier"

#: EMF namespace the memtier sidecar publishes client-side latency under. Ours,
#: not AWS's -- ecs.tf writes it, so the two must agree.
LOADGEN_NAMESPACE = "MemoryDB/LoadGenerator"


# --- Dimension string helpers ------------------------------------------------
#
# CloudWatch rows arrive with Dimensions serialised as "Key=Value;Key=Value",
# sorted by key, which is what the exporter writes into the CSV.

def cluster_dim(cluster_name: str) -> str:
    """Exact cluster-level dimension string for *cluster_name*."""
    return f"{CLUSTER_DIMENSION}={cluster_name}"


def node_dim(cluster_name: str, node_id: str) -> str:
    """Exact node-level dimension string.

    Both parts are required: MemoryDB does not publish a node-level metric
    under the node dimension alone.
    """
    return f"{CLUSTER_DIMENSION}={cluster_name};{NODE_DIMENSION}={node_id}"


#: Regex matching a cluster-level (not node-level) dimension string, i.e. the
#: aggregate row rather than one node's row.
CLUSTER_ONLY_PATTERN = rf"^{CLUSTER_DIMENSION}=[^;]+$"

#: Regex matching a node-level dimension string.
NODE_PATTERN = rf"^{CLUSTER_DIMENSION}=[^;]+;{NODE_DIMENSION}=[^;]+$"


# --- Metric names that differ from the ElastiCache originals -----------------
#
# Kept as constants rather than spelled inline so the delta stays greppable and
# a future third product does not have to re-find them.

KEYSPACE_HITS = "KeyspaceHits"      # was CacheHits
KEYSPACE_MISSES = "KeyspaceMisses"  # was CacheMisses
BYTES_USED = "BytesUsedForMemoryDB"  # was BytesUsedForCache

#: Server-side latency, MemoryDB's replacement for the three *CmdsLatency
#: metrics. Microseconds, true percentiles, and Valkey 7.2+ only -- absent
#: entirely on Redis OSS. Never blend these two into one figure: MemoryDB
#: commits every write to a multi-AZ transaction log, so writes run roughly an
#: order of magnitude slower than reads by design.
READ_LATENCY = "SuccessfulReadRequestLatency"
WRITE_LATENCY = "SuccessfulWriteRequestLatency"

#: Published by MemoryDB and by nothing in the ElastiCache project. WP7 turns
#: these into validity gates; they are listed here so the exporter contract can
#: name them without hardcoding strings in two places.
REPLICATION_METRICS = (
    "ReplicationBytes",
    "MaxReplicationThroughput",
    "ReplicationDelayedWriteCommands",
    "ReplicationLag",
    "IsPrimary",
    "PrimaryLinkHealthStatus",
)

#: Only published on data-tiering clusters, alongside the Tier dimension.
DATA_TIERING_METRICS = (
    "BytesReadFromDisk",
    "BytesWrittenToDisk",
    "NumItemsReadFromDisk",
    "NumItemsWrittenToDisk",
)
