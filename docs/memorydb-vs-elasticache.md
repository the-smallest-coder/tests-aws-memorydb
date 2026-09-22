# MemoryDB vs ElastiCache — porting reference

Everything the implementing agent needs that would otherwise require re-researching AWS
docs. Facts marked **TBD** are unverifiable without AWS CLI access and are resolved by
WP0.

Sources are listed at the bottom. Verified 2026-09-22 against AWS documentation.

---

## 1. Product semantics — why this is not a drop-in swap

MemoryDB is a **durable database**, not a cache. Every write is committed to a
multi-AZ transaction log before it is acknowledged.

| | ElastiCache (Redis/Valkey) | MemoryDB |
|---|---|---|
| Durability | Optional snapshots; data loss on failure is expected | Multi-AZ transaction log; writes durable on ack |
| Write latency | Sub-millisecond | Single-digit milliseconds |
| Read latency | Sub-millisecond | Sub-millisecond |
| Topology | Cluster mode optional | Always sharded |
| Multi-AZ | Optional | Always |
| Encryption at rest | Optional | Always on |
| TLS | Optional, default off | Default **on** |
| Auth | Optional auth token | ACL always required |

**AWS's published latency figures** (r6g family, `redis-benchmark`, 3M keys, no
pipelining): reads p50 ≈ 0.3 ms / p90 ≈ 0.4 ms; writes p50 ≈ 3.4–4.5 ms /
p90 ≈ 3.8–5.8 ms.

Use these as sanity bounds. A run reporting sub-millisecond MemoryDB writes has a
measurement bug.

---

## 2. CloudWatch

| | ElastiCache | MemoryDB |
|---|---|---|
| Namespace | `AWS/ElastiCache` | `AWS/MemoryDB` |
| Cluster dimension | `ReplicationGroupId` | `ClusterName` |
| Node dimension | `CacheClusterId` | `NodeId` |
| Extra dimension | — | `Tier` (`Memory` / `SSD`) on data-tiering clusters |

Node-level queries use `ClusterName` **and** `NodeId` together.

### 2.1 Renamed — these break inherited code

| ElastiCache | MemoryDB |
|---|---|
| `CacheHits` | `KeyspaceHits` |
| `CacheMisses` | `KeyspaceMisses` |
| `BytesUsedForCache` | `BytesUsedForMemoryDB` |

### 2.2 Absent on MemoryDB — remove from contracts and delete the panels

`CacheHitRate` (derive from `KeyspaceHits`/`KeyspaceMisses`),
`GetTypeCmdsLatency`, `SetTypeCmdsLatency`, `StringBasedCmdsLatency`,
`DatabaseMemoryUsageCountedForEvictPercentage`,
`DatabaseCapacityUsageCountedForEvictPercentage`, `TrafficManagementActive`.

The three `*CmdsLatency` metrics are the notable loss — MemoryDB's replacement is
`SuccessfulReadRequestLatency` / `SuccessfulWriteRequestLatency`, which are better
(true percentiles, microsecond units) but **engine-gated**, see §2.5.

### 2.3 Carried over unchanged

`CPUUtilization`, `EngineCPUUtilization`, `Evictions`, `CurrConnections`, `CurrItems`,
`NewConnections`, `MemoryFragmentationRatio`, `DatabaseMemoryUsagePercentage`,
`DatabaseCapacityUsagePercentage`, `StringBasedCmds`, `GetTypeCmds`, `SetTypeCmds`,
`ErrorCount`, `SuccessfulReadRequestLatency`, `SuccessfulWriteRequestLatency`.

Command-type aggregates also present: `EvalBasedCmds`, `GeoSpatialBasedCmds`,
`HashBasedCmds`, `HyperLogLogBasedCmds`, `JsonBasedCmds`, `KeyBasedCmds`,
`ListBasedCmds`, `PubSubBasedCmds`, `SetBasedCmds`, `SortedSetBasedCmds`,
`StreamBasedCmds`, and the `SearchBased*` family.

### 2.4 New on MemoryDB — the reason for a separate project

| Metric | Unit | Why it matters here |
|---|---|---|
| `ReplicationBytes` | Bytes | Bytes primary sends to replicas; proxy for write load |
| `MaxReplicationThroughput` | Bytes/sec | **Write throttling above ~100 MBps.** Primary saturation gate |
| `ReplicationDelayedWriteCommands` | Count | Writes delayed by synchronous replication — the durability ceiling, directly observable |
| `ReplicationLag` | Seconds | Replica staleness |
| `IsPrimary` | 0/1 | Identifies the primary node per shard |
| `PrimaryLinkHealthStatus` | 0/1 | Primary/engine sync health |
| `Reclaimed` | Count | Key expiration events |
| `ActiveDefragHits` | Count | Defrag activity |
| `DB0AverageTTL` | ms | Keyspace TTL profile |
| `BytesReadFromDisk`, `BytesWrittenToDisk`, `NumItemsReadFromDisk`, `NumItemsWrittenToDisk` | Bytes/Count | Data-tiering (`db.r6gd`) only |
| `AuthenticationFailures`, `CommandAuthorizationFailures`, `KeyAuthorizationFailures` | Count | ACL enforcement is mandatory, so these are live signals |

### 2.5 Two measurement traps

**Engine gate.** `SuccessfulReadRequestLatency` and `SuccessfulWriteRequestLatency` are
available **Valkey 7.2 onwards**. On the Redis OSS engine they do not exist. This is the
reason Valkey is this project's default engine.

**Pipelining inflation.** AWS documents that both metrics read high when the client
pipelines with `CLIENT REPLY` enabled. The inherited memtier config uses `--pipeline=N`.
Either pin `--pipeline=1` for server-latency runs or mark those figures untrustworthy in
the report. Do not report them silently.

### 2.6 Host-level metrics — **TBD**

`FreeableMemory`, `NetworkBytesIn`, `NetworkBytesOut`, `SwapUsage` and the
network-allowance-exceeded family live on a separate "Host-Level Metrics" page. Confirm
presence and exact names in WP0 before putting them in a contract.

---

## 3. Terraform

| ElastiCache resource | MemoryDB resource |
|---|---|
| `aws_elasticache_replication_group` | `aws_memorydb_cluster` |
| `aws_elasticache_subnet_group` | `aws_memorydb_subnet_group` |
| `aws_elasticache_parameter_group` | `aws_memorydb_parameter_group` |
| — | `aws_memorydb_acl`, `aws_memorydb_user` (optional; `open-access` exists) |

### 3.1 `aws_memorydb_cluster`

**Required:** `acl_name`, `node_type`.

**Optional, relevant here:** `engine` (`redis` | `valkey`), `engine_version`,
`num_shards` (default 1), `num_replicas_per_shard` (default **1**), `port` (default 6379,
forces replacement), `tls_enabled` (default **true**), `parameter_group_name`,
`subnet_group_name`, `security_group_ids`, `snapshot_retention_limit` (default 0),
`snapshot_window`, `maintenance_window`, `kms_key_arn`, `data_tiering`,
`auto_minor_version_upgrade`, `final_snapshot_name`, `sns_topic_arn`, `ip_discovery`,
`network_type`, `multi_region_cluster_name`.

**Exported:** `arn`, `cluster_endpoint { address, port }`, `engine_patch_version`,
`shards[] { name, num_nodes, slots, nodes[] { availability_zone, name, endpoint, … } }`.

### 3.2 Arguments with no MemoryDB equivalent — delete, don't emulate

- **Log delivery.** No slow-log or engine-log CloudWatch argument exists. The source
  project's `aws_cloudwatch_log_group.elasticache` and its two
  `log_delivery_configuration` blocks have nowhere to go.
- **AZ placement.** No `preferred_cache_cluster_azs` equivalent. Placement cannot be
  requested — but it **can be read back** from `shards[].nodes[].availability_zone`.
  Record it post-hoc; that preserves comparability checking even without control.
- **Failover toggles.** `automatic_failover_enabled` / `multi_az_enabled` — always on.
- **At-rest encryption toggle.** Always on; only the KMS key is configurable.

### 3.3 ACLs

Every account gets an immutable `open-access` ACL containing the `default` user.
A cluster must name an ACL. Clusters with `tls_enabled = false` **must** use
`open-access`. For benchmarking, `acl_name = "open-access"` is the right default —
keep it overridable.

---

## 4. Control-plane API (boto3 / IAM)

| ElastiCache | MemoryDB |
|---|---|
| `boto3.client("elasticache")` | `boto3.client("memorydb")` |
| `delete_replication_group(ReplicationGroupId=…)` | `delete_cluster(ClusterName=…)` |
| `describe_replication_groups(ReplicationGroupId=…)` | `describe_clusters(ClusterName=…)` |
| `ReplicationGroupNotFoundFault` | `ClusterNotFoundFault` |
| `elasticache:DeleteReplicationGroup` | `memorydb:DeleteCluster` |
| `elasticache:DescribeReplicationGroups` | `memorydb:DescribeClusters` |

Member-node enumeration changes shape: ElastiCache returns `MemberClusters` (a flat list
of cache cluster IDs); MemoryDB returns `Shards[].Nodes[]`. The exporter's
`_fallback_member_clusters` derivation does not transfer — replace it.

---

## 5. Pricing

Two dimensions, not one:

1. **On-demand node-hours**, per node, per hour.
2. **Data written**, per GB. This is the durability cost and has no ElastiCache analogue.

Pricing API service code: `AmazonMemoryDB` (was `AmazonElastiCache`). The
`cacheEngine` filter does not apply. `instanceType` values are `db.*`.

Current rates: **TBD** (regional and tiered — WP0).

A node-hours-only cost model understates MemoryDB under write-heavy load and biases any
comparison in its favour. WP8 exists to prevent that.

---

## 6. Node types

Families: `db.t4g`, `db.r6g`, `db.r7g`, `db.r6gd` (data tiering, SSD-backed).

There is **no `db.t4g.micro`** equivalent to ElastiCache's `cache.t4g.micro` default, and
no `t2`/`t3`/`m4`/`m5`/`m7g` families at all. The inherited `_node_memory_bytes` catalog
transfers **zero** entries.

Exact catalog and per-type usable memory in bytes: **TBD** (WP0).

Burstable prefix tuple becomes `("db.t4g.",)` — confirm against the WP0 catalog and
cover it with a test.

---

## Sources

- [Monitoring CloudWatch metrics — Amazon MemoryDB](https://docs.aws.amazon.com/memorydb/latest/devguide/cloudwatchmetrics.html)
- [Metrics for MemoryDB](https://docs.aws.amazon.com/memorydb/latest/devguide/metrics.memorydb.html)
- [Which Metrics Should I Monitor? — Amazon MemoryDB](https://docs.aws.amazon.com/memorydb/latest/devguide/metrics.whichshouldimonitor.html)
- [Authenticating users with Access Control Lists — Amazon MemoryDB](https://docs.aws.amazon.com/memorydb/latest/devguide/clusters.acls.html)
- [`aws_memorydb_cluster` resource docs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/memorydb_cluster)
- [Measuring database performance of Amazon MemoryDB for Redis — AWS Database Blog](https://aws.amazon.com/blogs/database/measuring-database-performance-of-amazon-memorydb-for-redis/)
- [Amazon MemoryDB pricing](https://aws.amazon.com/memorydb/pricing/)
