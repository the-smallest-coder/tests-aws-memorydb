# WP0 — AWS fact verification

The implementing agent **must not run these**. The repo owner runs them and the output
lands in `docs/verified/`, which the agent then reads.

Every command writes to a file via `tee`, so output is reviewable and diffable rather
than pasted into a chat.

Set your region first:

```bash
export AWS_REGION=us-east-1
mkdir -p docs/verified
```

---

## 1. Engine versions and parameter-group families

Resolves: which engine versions exist, whether Valkey 7.2+ is available (required for the
server-side latency metrics), and the exact parameter-group family strings.

```bash
aws memorydb describe-engine-versions --region "$AWS_REGION" --output json | tee docs/verified/engine-versions.json
```

## 2. Default parameter groups

Cross-checks the family names from §1 against what actually exists.

```bash
aws memorydb describe-parameter-groups --region "$AWS_REGION" --output json | tee docs/verified/parameter-groups.json
```

## 3. ACLs

Confirms `open-access` exists in this account.

```bash
aws memorydb describe-acls --region "$AWS_REGION" --output json | tee docs/verified/acls.json
```

## 4. Node type catalog

The authoritative list of `db.*` types purchasable in this region.

```bash
aws pricing get-attribute-values --service-code AmazonMemoryDB --attribute-name instanceType --region us-east-1 --output json | tee docs/verified/node-types.json
```

> `--region us-east-1` is correct here regardless of your test region — the Price List
> API is only served from a few endpoints. This mirrors the source project's
> `fetch_elasticache_price.sh`.

## 5. Node pricing and memory

Per-type hourly price **and** the memory attribute used to build `_node_memory_bytes`.
Run once per node type you intend to test; `db.r6g.large` shown as the example.

```bash
aws pricing get-products --service-code AmazonMemoryDB --region us-east-1 --format-version aws_v1 --filters "Type=TERM_MATCH,Field=instanceType,Value=db.r6g.large" "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" --output json | tee docs/verified/price-db-r6g-large.json
```

Change `location` to match `AWS_REGION` using the same region→location table the source
project keeps in `scripts/fetch_elasticache_price.sh`.

## 6. Data-written pricing

The second cost dimension, which has no ElastiCache equivalent. This is the one most
likely to need a manual read of the pricing page if the API shape is awkward.

```bash
aws pricing get-products --service-code AmazonMemoryDB --region us-east-1 --format-version aws_v1 --filters "Type=TERM_MATCH,Field=location,Value=US East (N. Virginia)" --output json | tee docs/verified/pricing-all.json
```

Then find the usage type covering data written (look for a `GB`-unit dimension rather
than `Hrs`).

## 7. Host-level metric names — requires a live cluster

Deferred until the first real run exists. Resolves whether `FreeableMemory`,
`NetworkBytesIn`/`Out`, `SwapUsage` and the network-allowance-exceeded family are
published, and under exactly which names.

```bash
aws cloudwatch list-metrics --namespace AWS/MemoryDB --region "$AWS_REGION" --output json | tee docs/verified/published-metrics.json
```

Run this **while a cluster is up and under load** — CloudWatch only lists metrics that
have recent datapoints. Running it against a torn-down cluster returns an incomplete list
and will produce a wrong contract.

---

## Closing WP0

1. All files above present in `docs/verified/`.
2. Every `TBD` in `docs/memorydb-vs-elasticache.md` replaced with a verified value.
3. Any documented fact the CLI output contradicts is corrected there, with the
   contradiction noted inline — a silent correction loses the signal that AWS docs and
   AWS behaviour disagreed.

Item 7 may close later than 1–6; it gates WP5's contract lists, not WP1–WP4.
