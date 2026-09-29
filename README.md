# AWS MemoryDB Performance Testing Infrastructure

Terraform infrastructure for **automated** AWS MemoryDB performance testing.

> `terraform apply` → auto-run load tests → auto-export metrics → auto-cleanup → `terraform destroy`

---

## Related project

| | |
|---|---|
| **This repo** | AWS MemoryDB — durable, multi-AZ transaction log, always sharded |
| **[tests-aws-elasticache-redis](https://github.com/the-smallest-coder/tests-aws-elasticache-redis)** | AWS ElastiCache (Redis / Valkey) — cache semantics, optional durability |

The two are **not interchangeable**, and their numbers are not directly comparable.
MemoryDB commits every write to a multi-AZ transaction log before acknowledging it, so
its write latency is structurally around an order of magnitude higher than ElastiCache's
— by design, not as a defect. AWS's own published figures put MemoryDB reads at
sub-millisecond and writes at single-digit milliseconds.

Reports from the two projects are built to be laid side by side and read by a human.

Pick MemoryDB when you need durability. Pick ElastiCache when you need a cache. Then use
the matching repo to find out what it costs you.

---

## 🎯 What It Does

1. **Provisions** a MemoryDB cluster + ECS Fargate load generators
2. **Runs** `memtier_benchmark` for a configurable duration
3. **Shuts down** by stopping ECS and deleting the MemoryDB cluster
4. **Verifies** both are gone, then **exports** metrics (CSV) + logs to S3 and generates
   the HTML report

Auto-shutdown and teardown verification are cost-safety features. MemoryDB's smallest
node is larger and more expensive than ElastiCache's, and its default is **one replica
per shard** — so the default config is a two-node cluster, and one left running costs
real money. Nodes billed is `num_shards × (num_replicas_per_shard + 1)`; the report and
`cluster_details.json` both state it explicitly rather than implying it.

---

## 📋 Prerequisites

- Terraform >= 1.0
- AWS CLI configured
- Existing VPC + private subnets
- S3 bucket for exports

---

## 🔧 Key differences from the ElastiCache project

These follow from MemoryDB's design and are not configurable:

| | |
|---|---|
| **Always sharded** | No non-cluster mode. Clients must be cluster-aware. |
| **TLS on by default** | `tls_enabled` defaults to true. |
| **ACL required** | Every cluster names an ACL; `open-access` is the benchmarking default. |
| **Always multi-AZ, always encrypted at rest** | No toggles. |
| **No slow-log / engine-log export** | No CloudWatch log-delivery equivalent exists. |
| **Two-term pricing** | Node-hours **plus** per-GB data written. |
| **No AZ pinning** | Placement is observed after the fact, per node, into `cluster_details.json`. |

## Read and write latency are reported separately, always

MemoryDB commits every write to a multi-AZ transaction log before acknowledging it,
so write latency sits roughly an order of magnitude above read latency — by design.
There is no combined "latency" figure anywhere in the report: not in the charts, not
in the top cards, not in the comparison verdict. A single blended number would
describe neither and would read as a defect rather than as the durability trade-off
it is.

---

## Report window

Inherited unchanged from the source project, and non-negotiable:

- **Start**: timestamp of the very first memtier log message.
- **End**: timestamp of the very last memtier log message.
- Every plotted value sits at the absolute timestamp from its source log or metric row.

No relative offsets, reconstructed timestamps, or duration-derived windows anywhere.

---

## License

See [LICENSE](LICENSE).
