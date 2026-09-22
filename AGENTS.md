# AWS MemoryDB Performance Testing — Agent Instructions

Terraform infrastructure for automated MemoryDB load testing.
Single `terraform apply` → MemoryDB cluster + ECS `memtier_benchmark` → run → auto-export
metrics/logs to S3 → stop ECS and delete the cluster.

**Read [PLAN.md](PLAN.md) before doing anything.** This repo is a scaffold copied from the
ElastiCache project; most files still contain ElastiCache code. The plan says what to
change, in what order, and what "done" means for each step.

## Non-negotiables

### AWS CLI is run by the repo owner, never by an agent
Never invoke `aws` through any tool. When AWS output is needed, provide the exact command
and let the owner run it, piped through `tee <repo-relative-path>` so the result is saved
to a file you can read. `docs/wp0-verification.md` is the worked example.

### Absolute timestamps only
- Report window start = timestamp of the first memtier log message across all streams.
- Report window end = timestamp of the last memtier log message across all streams.
- Every plotted and reported datapoint uses the absolute timestamp from its own log or
  metric record.
- No relative offsets, reconstructed timestamps, duration-derived windows, prefill
  windows, or "N minutes after start" interpretations. Anywhere.

### Fail loudly
An unknown node type, an unmatched metric name, or a dimension mismatch must raise and
name the supported set. Never fall back to a default, never skip silently. The source
project's burstable-prefix bug — a hardcoded `cache.t4g.` tuple that would match nothing
here and drop metrics without a word — is the pattern to avoid.

### Never blend read and write latency
MemoryDB writes are structurally ~10x slower than reads because of the multi-AZ
transaction log. A single combined latency figure is misinformation. Separate panels,
separate verdicts, everywhere including top cards.

## Architecture (target state)

| Component | File(s) | Purpose |
|-----------|---------|---------|
| MemoryDB cluster | `main.tf` | Cluster under test |
| ECS load generator | `ecs.tf` | Fargate tasks running `memtier_benchmark` |
| Security groups | `security_groups.tf` | Cluster SG allows TCP 6379 from load-gen SG |
| Lambda orchestration | `lambda_shutdown.tf`, `lambda/shutdown.py` | Export then delete cluster |
| Scheduling | `lambda/schedule_shutdown.py` | Sets EventBridge cron at apply time |
| Post-test verify | `lambda/verify_shutdown.py` | SES alert if resources still running |
| Reporter | `reporter/report_generator.py` | HTML reports from metrics CSV + logs |

## Key conventions (inherited)

- **Cluster ID**: `{project_name}-memorydb-{last-8-digits-of-timestamp}`. Keep within 40
  characters — MemoryDB's cluster-name limit, same as ElastiCache's.
- **Run folders**: `results/YYYYmmdd-HHMMSS/`
- **Metrics CSV**: `Timestamp,Namespace,MetricName,Stat,Value,Unit,Dimensions`
- **results_local.json**: strict superset of the source project's schema v4. MemoryDB-only
  fields go under a `memorydb` top-level key — never redefine an existing key.

## MemoryDB gotchas

- **Always sharded.** No non-cluster mode; memtier always needs `--cluster-mode`.
- **`num_replicas_per_shard` defaults to 1**, so the naive config is a 2-node cluster.
- **`tls_enabled` defaults to true**, so memtier needs `--tls`.
- **`acl_name` is required.** `open-access` is the benchmarking default.
- **Server-side latency metrics need Valkey 7.2+.** They do not exist on Redis OSS.
- **Pipelining inflates those metrics.** `--pipeline=N` makes
  `Successful{Read,Write}RequestLatency` read high. Pin `--pipeline=1` or mark the run.
- **No log delivery, no AZ pinning.** Both are absent from `aws_memorydb_cluster`.
- **EventBridge placeholder cron** (inherited): the shutdown rule starts at
  `cron(0 0 1 1 ? 2099)` and is rewritten by the `schedule_shutdown` Lambda at apply time.

## Reference

- [`PLAN.md`](PLAN.md) — work breakdown and exit criteria
- [`docs/memorydb-vs-elasticache.md`](docs/memorydb-vs-elasticache.md) — verified porting delta
- [`docs/wp0-verification.md`](docs/wp0-verification.md) — AWS facts still to confirm
