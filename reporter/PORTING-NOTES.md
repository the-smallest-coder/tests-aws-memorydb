# Reporter porting notes (WP6)

This directory is a **verbatim copy** of the ElastiCache project's reporter at its `HEAD`.
It does not work against MemoryDB. Treat it as a starting point, not as code.

Read [`../docs/memorydb-vs-elasticache.md`](../docs/memorydb-vs-elasticache.md) first —
it has the full metric mapping this checklist refers to.

## Scale of the change

Occurrences of ElastiCache-specific literals (`CacheClusterId`, `ReplicationGroupId`,
`AWS/ElastiCache`, `CacheHits`, `CacheMisses`, `CacheHitRate`, `BytesUsedForCache`,
`*CmdsLatency`, `TrafficManagementActive`, `*CountedForEvict*`, `cache.t*`):

| File | Hits |
|---|---:|
| `exporter.py` | 24 |
| `summary.py` | 20 |
| `helpers.py` | 18 |
| `charts.py` | 15 |
| `report_generator.py` | 12 |
| `cards.py` | 6 |
| `fetch_missing_metrics.py` | 6 |
| `report_ecs.py` | 6 |
| `report_compare.py` | 1 |
| **reporter total** | **108** |
| `../tests/` total | **98** |

Regenerate this count any time:

```bash
grep -rc "CacheClusterId\|ReplicationGroupId\|AWS/ElastiCache\|CacheHits\|CacheMisses\|CacheHitRate\|BytesUsedForCache\|CmdsLatency\|TrafficManagementActive\|CountedForEvict\|cache\.t" reporter/ tests/
```

## Do this first: kill the literals

Every `metric_filter(...)` call passes a dimension name as a bare string. Do **not**
find-and-replace `'CacheClusterId'` → `'NodeId'`. Introduce a single source of truth:

```python
# reporter/metric_profile.py
NAMESPACE          = "AWS/MemoryDB"
CLUSTER_DIMENSION  = "ClusterName"
NODE_DIMENSION     = "NodeId"
```

and route everything through it. A bare dimension-name string literal anywhere in
`reporter/` after WP6 is a bug. The grep in the WP6 exit criteria enforces this.

Reason: the source project's literals are why this port is a 200-occurrence job rather
than a three-line change. Reproducing that coupling in a new repo wastes the one chance
to not have it.

## Then: the renames

| Replace | With |
|---|---|
| `CacheHits` | `KeyspaceHits` |
| `CacheMisses` | `KeyspaceMisses` |
| `BytesUsedForCache` | `BytesUsedForMemoryDB` |
| `CacheHitRate` | derive from `KeyspaceHits` / `KeyspaceMisses` |

`helpers.py` already derives hit rate from the hits/misses pair — that logic survives, only
its input names change.

## Then: the deletions

MemoryDB does not publish these. Remove the contract entries **and** the panels that
render them. An empty chart is worse than an absent one.

- `GetTypeCmdsLatency`, `SetTypeCmdsLatency`, `StringBasedCmdsLatency`
  → replaced by `SuccessfulReadRequestLatency` / `SuccessfulWriteRequestLatency`
- `DatabaseMemoryUsageCountedForEvictPercentage`
- `DatabaseCapacityUsageCountedForEvictPercentage`
- `TrafficManagementActive`

## Landmines

**`exporter.py` `value_failures` gate.** It asserts `CacheHits`/`CacheMisses` have
positive `Sum` datapoints. On MemoryDB those metrics do not exist, so this fails on
**every** run. Repoint it at `KeyspaceHits`/`KeyspaceMisses`.

**`exporter.py` `BURSTABLE_NODE_TYPE_PREFIXES`.** Currently `("cache.t2.", "cache.t3.",
"cache.t4g.")`. Against `db.t4g.*` it matches nothing and silently drops the CPU-credit
metrics from the contract — no error, no warning. Becomes `("db.t4g.",)`, and WP2
requires a test proving every burstable family in the catalog matches.

**`exporter.py` `_fallback_member_clusters`.** Derives node IDs from the replication-group
ID by ElastiCache's `-001`/`-002` convention. MemoryDB returns `Shards[].Nodes[]` instead.
Delete the derivation; read the real shape.

**`fetch_missing_metrics.py`.** Hardcodes namespace and dimension at module level. The
source project's own tech-debt audit already flagged this file as an untested manual
workaround — porting it is optional. Consider deleting it rather than carrying it.

**Keep `list_metrics` unfiltered.** `exporter.py` deliberately passes no metric-name
filter so discovery adapts to whatever AWS publishes. That design is the reason this port
is tractable at all. Do not "optimise" it into an explicit name list.

## Tests

`tests/` is copied too and fails for the same reasons. Port it in the same work package —
the WP6 exit criteria require a green suite, not a follow-up ticket.

`tests/test_elasticache_node_catalog.py` needs renaming along with its subject.
