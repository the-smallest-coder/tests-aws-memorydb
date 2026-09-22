# Plan: AWS MemoryDB Performance Testing Project

Derived from [`tests-aws-elasticache-redis`](https://github.com/the-smallest-coder/tests-aws-elasticache-redis).
This document is the build instruction for the agent implementing this repository.

## Goal

A standalone Terraform project that benchmarks **AWS MemoryDB** the way the source
project benchmarks ElastiCache: one `terraform apply` provisions the cluster and ECS
`memtier_benchmark` load generators, runs for a fixed duration, auto-shuts-down,
verifies teardown, exports metrics and logs to S3, and produces an HTML report.

The two projects stay independent. The only coupling is:

1. A README cross-reference in each direction.
2. **The report output must be visually comparable** — a human puts two HTML reports
   side by side and the sections, units, and framing line up.
3. `results_local.json` stays a strict superset of the source project's **schema v4**,
   so the later programmatic-comparison phase is possible without a rewrite.

## Relationship To The Source Project

| | |
|---|---|
| Code sharing | **None.** This repo owns its copy. Fixes are not automatically shared. |
| Baseline | Copied from source `HEAD` (commit `4891d60`), *not* its dirty working tree. |
| Divergence | Free and expected. Do not try to keep files byte-identical. |
| Cross-link | README here points at the ElastiCache project as the non-durable alternative, and vice versa. |

> The source project had an in-flight branch (`feature/report-metrics-refactor`) at copy
> time. Its work is **not** in this baseline. If that branch lands improvements worth
> having, port them deliberately — do not blind-merge.

## Hard Requirements

1. **No AWS CLI calls by the implementing agent.** The repo owner runs every `aws`
   command. Any fact that needs AWS to answer goes through WP0. See `AGENTS.md`.
2. **Absolute timestamps only**, inherited verbatim from the source project. Report
   window start/end are the first and last memtier log message timestamps. No relative
   offsets, no reconstructed timestamps, no duration-derived windows.
3. **No silent degradation.** An unknown node type, an unmatched metric name, or a
   dimension mismatch must fail loudly and name the supported set. This is the single
   biggest defect class inherited from the source project — see *Traps* below.
4. **Read and write latency are never blended into one verdict.** MemoryDB's durability
   design makes write latency structurally ~10x read latency. A single "latency" number
   is misinformation.
5. **Cost must include the data-written term.** Node-hours alone systematically
   understates MemoryDB.
6. `results_local.json` is a **superset** of source schema v4. MemoryDB-only fields live
   under their own top-level key, never by redefining an existing one.
7. Auto-shutdown and teardown verification are safety features, not conveniences. They
   are not optional in any work package.

## Current Evidence

### Verified from AWS documentation (2026-09-22)

**CloudWatch.** Namespace `AWS/MemoryDB`. Dimensions `ClusterName` and `NodeId`
(node-level). Data-tiering clusters add a `Tier` dimension with values `Memory` / `SSD`.

**Terraform.** `aws_memorydb_cluster` requires `acl_name` and `node_type`.
`tls_enabled` defaults to **true**. `num_shards` defaults to 1, `num_replicas_per_shard`
defaults to **1** (not 0 — differs from the ElastiCache default the source project
assumes). There is **no log-delivery argument** and **no availability-zone placement
argument**. The `shards` attribute exposes each node's availability zone, and
`cluster_endpoint` exposes `address` / `port`.

**ACLs.** Every account has an immutable `open-access` ACL containing the `default`
user. A cluster must name an ACL. Clusters with TLS disabled *must* use `open-access`.

**Latency baseline (AWS's own published benchmark).** Reads p50 ≈ 0.3 ms, p90 ≈ 0.4 ms.
Writes p50 ≈ 3.4–4.5 ms, p90 ≈ 3.8–5.8 ms, on r6g nodes. Use these as sanity bounds:
a MemoryDB run reporting sub-millisecond writes is measuring something wrong.

**Pricing.** Two dimensions: on-demand node-hours **plus** a per-GB charge for data
written. Exact rates are regional and tiered — WP0 verifies them.

### Not yet verified — WP0 blocks on these

- Exact `db.*` node type catalog and each type's usable memory in bytes.
- Available engine versions and parameter-group family names.
- Whether host-level metrics (`FreeableMemory`, `NetworkBytesIn/Out`, `SwapUsage`,
  network-allowance-exceeded family) are published, and under which names.
- Current data-written price per GB and node-hour prices.

## The Metric Delta — Read This Before Touching The Reporter

Full table in [`docs/memorydb-vs-elasticache.md`](docs/memorydb-vs-elasticache.md).
The renames that break inherited code:

| Source project expects | MemoryDB publishes | Consequence if not fixed |
|---|---|---|
| `CacheHits` / `CacheMisses` | **`KeyspaceHits` / `KeyspaceMisses`** | Hit-rate derivation returns empty; the exporter's `value_failures` gate fails on **every** run |
| `CacheHitRate` | *absent* — must be derived | Silent empty panel |
| `BytesUsedForCache` | **`BytesUsedForMemoryDB`** | Memory summary empty |
| `GetTypeCmdsLatency`, `SetTypeCmdsLatency`, `StringBasedCmdsLatency` | *absent* | Three latency panels render empty; these are ElastiCache-only |
| `DatabaseMemoryUsageCountedForEvictPercentage`, `DatabaseCapacityUsageCountedForEvictPercentage` | *absent* | Contract reports false "missing" |
| `TrafficManagementActive` | *absent* | Contract reports false "missing" |

Carried over unchanged: `CPUUtilization`, `EngineCPUUtilization`, `Evictions`,
`CurrConnections`, `CurrItems`, `NewConnections`, `MemoryFragmentationRatio`,
`DatabaseMemoryUsagePercentage`, `DatabaseCapacityUsagePercentage`, `StringBasedCmds`,
`GetTypeCmds`, `SetTypeCmds`, `ErrorCount`, `SuccessfulReadRequestLatency`,
`SuccessfulWriteRequestLatency`.

New and MemoryDB-specific — these are the reason this project exists as its own repo:
`ReplicationBytes`, `MaxReplicationThroughput`, `ReplicationDelayedWriteCommands`,
`ReplicationLag`, `IsPrimary`, `PrimaryLinkHealthStatus`, `Reclaimed`,
`ActiveDefragHits`, and the data-tiering family (`BytesReadFromDisk`,
`BytesWrittenToDisk`, `NumItemsReadFromDisk`, `NumItemsWrittenToDisk`).

## Traps

Each of these produces a **plausible-looking but wrong** report. Verify every one.

1. **`SuccessfulReadRequestLatency` / `SuccessfulWriteRequestLatency` require Valkey 7.2+.**
   On Redis OSS engine they do not exist. This is why Valkey is the default engine here.
2. **Pipelining inflates the server-side latency metrics.** AWS documents that these two
   metrics read high when the client pipelines with `CLIENT REPLY` enabled. The source
   project runs memtier with `--pipeline=N` by default. Either pin `--pipeline=1` for
   runs whose purpose is server latency, or record prominently in the report that
   server-side latency is not trustworthy for that run. Do not silently report both.
3. **`num_replicas_per_shard` defaults to 1.** An unmodified config costs double what the
   source project's single-node default costs.
4. **`tls_enabled` defaults to true**, so memtier must pass `--tls`. Getting this wrong
   produces a connection failure, not a wrong number — the benign case.
5. **Burstable prefix matching.** Source has `("cache.t2.", "cache.t3.", "cache.t4g.")`.
   Copying it verbatim silently matches nothing against `db.t4g.*`. Make a non-match of a
   known-burstable type a test failure, not a shrug.
6. **MemoryDB is always sharded.** There is no non-cluster mode. memtier always needs
   `--cluster-mode`; `cluster_mode_enabled` as a variable should not survive the port.
7. **`MaxReplicationThroughput` above ~100 MBps throttles writes.** A run that crosses it
   is measuring the throttle, not the node. This must be a first-class validity gate.

## Work Packages

Each package states its own exit criteria. Do not start a package whose predecessor's
exit criteria are unmet.

---

### WP0 — Verify AWS facts (BLOCKING)

Nothing else may hardcode an AWS constant until this closes.

1. Produce the command list in [`docs/wp0-verification.md`](docs/wp0-verification.md).
2. The repo owner runs them; output lands in `docs/verified/`.
3. Fill every `TBD` in `docs/memorydb-vs-elasticache.md` from that output.

**Exit criteria**
- `docs/verified/` contains the node-type, engine-version, parameter-family, ACL and
  pricing outputs.
- `docs/memorydb-vs-elasticache.md` contains zero `TBD` markers.
- Any documented fact that the CLI output contradicts is corrected in that file, with the
  contradiction noted.

---

### WP1 — Terraform control plane

Replace the ElastiCache resources with MemoryDB equivalents.

| Remove | Add |
|---|---|
| `aws_elasticache_replication_group` | `aws_memorydb_cluster` |
| `aws_elasticache_subnet_group` | `aws_memorydb_subnet_group` |
| `aws_elasticache_parameter_group` | `aws_memorydb_parameter_group` |
| `aws_cloudwatch_log_group.elasticache` + two `log_delivery_configuration` blocks | *nothing* — unsupported; delete, and say so in the README |
| `preferred_cache_cluster_azs` / `elasticache_pinned_az` | *nothing* — unsupported; replaced by WP2's post-hoc AZ recording |
| `num_cache_clusters`, `cluster_mode_enabled` | `num_shards`, `num_replicas_per_shard` |
| `automatic_failover_enabled`, `multi_az_enabled` | *nothing* — MemoryDB is always multi-AZ |
| `at_rest_encryption_enabled` | *nothing* — always on; optional `kms_key_arn` |
| `transit_encryption_enabled`, `auth_token` | `tls_enabled` (default true) + `acl_name` |

Set `acl_name = "open-access"` as the default, with a variable to override. Keep the
40-character cluster-id precondition; MemoryDB's own limit is 40 for cluster names too,
so the existing guard transfers.

**Consider the community module.** [`terraform-aws-modules/memory-db/aws`](https://registry.terraform.io/modules/terraform-aws-modules/memory-db/aws/latest)
already wraps the cluster, subnet group, parameter group, ACL and users. The source
project hand-rolls its ElastiCache resources, so hand-rolling here is the
consistent choice — but this is a rewrite, not a port, and the module removes the
largest block of unfamiliar surface. Decide explicitly and record the reason; do not
drift into hand-rolling just because the source project did.

**Exit criteria**
- `terraform validate` passes.
- `grep -r "aws_elasticache" *.tf` returns nothing.
- `terraform plan` against `terraform.tfvars.example` produces a plan containing
  `aws_memorydb_cluster`, and no provider errors.

---

### WP2 — Node catalog, capacity model, AZ recording

1. Replace `local._node_memory_bytes` with the verified `db.*` catalog from WP0. Delete
   every `cache.*` entry — do not keep them "just in case".
2. `BURSTABLE_NODE_TYPE_PREFIXES` becomes `("db.t4g.",)` — plus whatever else WP0 finds.
3. **Add a test** that every burstable family in the catalog matches the prefix tuple.
   This is the guard against trap #5.
4. Record actual AZ placement after apply by reading the `shards[].nodes[]` attribute
   into `cluster_details.json`. This replaces the AZ-pinning reproducibility the source
   project had and MemoryDB cannot offer: placement is observed rather than requested, so
   run-to-run comparability is *checkable* even though it is not *controllable*.

**Exit criteria**
- An unsupported `node_type` fails at plan time with an error naming the supported set.
- The burstable-prefix test exists and passes.
- `cluster_details.json` from a real run contains one AZ per node.

---

### WP3 — Load generator

Keep the memtier container, the EMF client-latency sidecar, and the per-task key-prefix
scheme as-is. Change:

- Endpoint: `aws_memorydb_cluster.main.cluster_endpoint[0].address`.
- Always pass `--cluster-mode`; remove the non-cluster branch.
- `--tls` follows `tls_enabled`, default on.
- Recompute the key-maximum fill model against MemoryDB's usable memory (WP0), not the
  inherited ElastiCache figures.

Address trap #2 explicitly: add a variable that pins `--pipeline=1`, and have the report
state which mode the run used.

**Exit criteria**
- A run connects and sustains load against a TLS-enabled, sharded MemoryDB cluster.
- The report records the pipeline setting and whether server-side latency is trustworthy
  for that run.

---

### WP4 — Lambda orchestration

Mechanical port, small surface.

| Source | Target |
|---|---|
| `boto3.client("elasticache")` | `boto3.client("memorydb")` |
| `delete_replication_group(ReplicationGroupId=…)` | `delete_cluster(ClusterName=…)` |
| `describe_replication_groups(…)` | `describe_clusters(…)` |
| `ReplicationGroupNotFoundFault` | `ClusterNotFoundFault` |
| IAM `elasticache:DeleteReplicationGroup`, `elasticache:DescribeReplicationGroups` | `memorydb:DeleteCluster`, `memorydb:DescribeClusters` |

Keep the EventBridge placeholder-cron mechanism and the SES notification flow unchanged.
Rename user-facing strings from "ElastiCache" to "MemoryDB" — the notification emails say
ElastiCache throughout.

**Exit criteria**
- Unit tests with a stubbed boto3 cover delete, describe, and not-found.
- `grep -ri "elasticache" lambda/ *.tf` returns nothing outside deliberate cross-reference prose.
- A real run tears the cluster down and the verify Lambda confirms it.

---

### WP5 — Metric export

1. Namespace → `AWS/MemoryDB`. Sources become `ClusterName` (cluster-level) and
   `ClusterName` + `NodeId` (per node).
2. Keep the source project's best idea untouched: `list_metrics` with **no** metric-name
   filter, so the export adapts to whatever AWS publishes.
3. Rewrite the three contract lists (`REQUIRED_*`, `REPORT_CONTRACT_METRICS`,
   `CORE_*_CONTRACT_METRICS`) from the delta table. Remove every metric MemoryDB does not
   publish — a contract full of false "missing" entries trains people to ignore it.
4. Replace the `value_failures` gate: `CacheHits`/`CacheMisses` → `KeyspaceHits`/`KeyspaceMisses`.
5. Add the conditional contract branch for data-tiering nodes (`Tier` dimension).

**Exit criteria**
- Export from a real run writes `rows_written > 0`.
- `missing_from_discovery` is empty on a healthy run.
- A deliberately wrong namespace produces a loud failure, not an empty CSV.

---

### WP6 — Reporter port

The copied `reporter/` is a **starting point carrying ~149 wrong literals**, not working
code. See `reporter/PORTING-NOTES.md`.

1. Introduce a single module-level metric-source profile (namespace, cluster dimension,
   node dimension) and route every `metric_filter` call through it. No bare
   `'CacheClusterId'` string literals anywhere.
2. Apply the metric renames from the delta table.
3. Delete panels for metrics MemoryDB does not publish. Do not leave empty charts.
4. Port the test suite alongside — a green suite is part of this package, not a follow-up.

**Exit criteria**
- `grep -rn "CacheClusterId\|ReplicationGroupId\|AWS/ElastiCache" reporter/` returns nothing.
- Test suite green.
- An HTML report renders from a real run with no empty sections.

---

### WP7 — Durability-aware reporting *(MemoryDB-specific)*

This is the package that justifies a separate project.

1. Read and write latency get separate panels and separate verdicts. The report must not
   emit a single blended latency figure anywhere — top cards included.
2. New validity gates, in the style of the source project's existing load-generator gates:
   - `MaxReplicationThroughput` approaching the throttle threshold → warn, and mark
     throughput conclusions untrustworthy.
   - `ReplicationDelayedWriteCommands > 0` → the run hit the durability ceiling; say so
     in plain language rather than burying it in a chart.
   - `ReplicationLag` and `PrimaryLinkHealthStatus` as health context.
3. Label the run's durability class in the report header so a reader comparing against an
   ElastiCache report cannot miss it.

**Exit criteria**
- A deliberately write-saturated run is flagged by both gates.
- No template path can produce a single combined latency number.
- The durability-class label appears in the header of every report.

---

### WP8 — Cost model

1. Pricing script: service code `AmazonMemoryDB`, `db.*` instance types, drop the
   `cacheEngine` filter.
2. Add the **data-written** term. Derive written volume from the run's own metrics
   (`ReplicationBytes` or the write-command counters — pick one, document why) and price
   it at the WP0-verified rate.
3. Cost section reports node-hours and data-written separately, then the total. A
   single opaque figure hides the term that differentiates MemoryDB.

**Exit criteria**
- A write-heavy run shows data-written as a non-trivial share of total cost.
- `cost.*` fields in `results_local.json` distinguish the two terms.
- Price-lookup failure degrades to `"unavailable"` without blocking apply or destroy —
  same contract as the source project's script.

---

### WP9 — Schema compatibility and cross-links

1. `results_local.json` keeps every schema-v4 key it can legitimately fill. MemoryDB-only
   output goes under a new top-level `memorydb` key.
2. Document the contract in `docs/results-schema.md`, including which v4 keys are
   permanently null here and why.
3. README cross-links both directions. State plainly that the two products are not
   interchangeable and why.

**Exit criteria**
- A schema-v4 consumer reads a MemoryDB `results_local.json` without crashing.
- Both READMEs link to each other.

---

### WP10 — Cross-product comparison *(later phase, not v1)*

Deferred by decision. Defined here so WP9 doesn't foreclose it.

When built, it must refuse to present read and write latency as one comparison, and must
label the durability difference on every comparison row rather than in a footnote. A
comparison that makes MemoryDB look slow without saying why is worse than no comparison.

## Test Matrix

Mirrored from the source project:

- Node types within a family, same load, same config.
- Valkey vs Redis OSS, same node type. *(Note: loses server-side latency metrics on
  Redis OSS — see trap #1.)*
- Shard count scaling.

MemoryDB-specific additions:

- **Write-ratio sweep.** The durability cost only shows up under writes. Sweep
  `--ratio` and plot write latency and data-written cost against it. This is the
  headline MemoryDB result.
- **Replication ceiling.** Drive write throughput until `ReplicationDelayedWriteCommands`
  goes positive; report where the ceiling sits per node type.
- **Failover under load.** Trigger a failover mid-run; measure client-visible impact.
- **Data tiering.** `db.r6gd` nodes with a working set larger than memory; report the
  disk-metric family.

## Validation

A build is done when all of the following hold on a **real run**, not a fixture:

1. `terraform apply` → load runs → auto-shutdown fires → verify Lambda confirms teardown
   → reporter produces HTML, with no manual intervention.
2. The HTML report has no empty sections and no placeholder text.
3. Read and write latency appear separately and are within an order of magnitude of AWS's
   published figures (reads sub-millisecond, writes single-digit milliseconds). Values far
   outside that range mean a measurement bug, not a discovery.
4. The cost section shows both pricing terms.
5. Putting this report beside an ElastiCache report from the source project, a reader can
   compare them without a key.
6. Test suite green.
7. `grep -ri "elasticache" .` returns only deliberate cross-reference prose.

## Out Of Scope

- Sharing code with the ElastiCache project, in either direction.
- Programmatic cross-product comparison (WP10, later phase).
- Changing the source project.
- MemoryDB Multi-Region clusters.
- Vector search / secondary-index workloads.
- Reconstructing timestamps, or any relative-time reporting.
