# Live AWS Price List lookup for this run's MemoryDB node hourly cost.
# Region- and engine-qualified rather than a hardcoded table. MemoryDB
# prices Valkey and Redis OSS node-hours as separate SKUs (the Price List
# `engine` attribute; Valkey is about 30% cheaper -- db.r6g.large in
# us-east-1 is $0.2163/h vs $0.309/h), so the script filters on engine_type.
#
# This covers node-hours only. MemoryDB's second pricing dimension, the
# per-GB charge for data written, is WP8 and is not looked up here.
#
# Shells out to `aws pricing get-products` because the Price List Query API
# has no native Terraform data source; the script always exits 0 and
# reports source: "unavailable" on any failure (missing perms, no network,
# no matching SKU), so a pricing hiccup never blocks `terraform apply`.
# Requires `bash`, `aws`, and `jq` on the machine running Terraform, and
# `pricing:GetProducts` on its credentials.
#
# The script's exit-0 contract only covers failures *inside* it -- it does
# not cover Terraform failing to *launch* the program at all (bash missing
# from PATH, this module's scripts/ directory unreadable). `data` blocks are
# re-evaluated on every plan, including `terraform destroy`, so a launch
# failure here could strand a running cluster the same way the AZ
# data-source read-back this repo avoids elsewhere would (see
# test_no_data_source_reads_back_the_cluster_shutdown_deletes). Gated by
# var.enable_price_lookup as an escape hatch for exactly that case.
data "external" "node_price" {
  count = var.enable_price_lookup ? 1 : 0

  program = ["bash", "${path.module}/scripts/fetch_memorydb_price.sh"]

  query = {
    node_type   = var.node_type
    engine_type = var.engine_type
    aws_region  = var.aws_region
  }
}

# CloudWatch Log Group for ECS Container Insights (managed for cleanup on destroy)
resource "aws_cloudwatch_log_group" "container_insights" {
  name              = "/aws/ecs/containerinsights/${local.cluster_id}-loadgen/performance"
  retention_in_days = var.cloudwatch_log_retention_days

  tags = {
    Name = "${local.cluster_id}-loadgen-container-insights"
  }
}

# ECS Cluster for load generators
resource "aws_ecs_cluster" "loadgen" {
  name = "${local.cluster_id}-loadgen"

  setting {
    name  = "containerInsights"
    value = var.ecs_container_insights_mode
  }

  configuration {
    execute_command_configuration {
      logging = "DEFAULT"
    }
  }

  # Ensure log group exists first and is deleted after the cluster.
  depends_on = [aws_cloudwatch_log_group.container_insights]

  tags = {
    Name = "${local.cluster_id}-loadgen"
  }
}

# CloudWatch Log Group for load generator (date-stamped)
resource "aws_cloudwatch_log_group" "loadgen" {
  name              = "/aws/ecs/${local.cluster_id}/loadgen"
  retention_in_days = var.cloudwatch_log_retention_days

  tags = {
    Name = "${local.cluster_id}-loadgen-logs"
  }
}

# ECS Task Definition for memtier_benchmark
resource "aws_ecs_task_definition" "loadgen" {
  family                   = "${local.cluster_id}-loadgen"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.loadgen_cpu
  memory                   = var.loadgen_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name        = "memtier"
      image       = var.loadgen_image
      essential   = true
      stopTimeout = 120

      entryPoint = ["sh", "-c"]
      command = [
        join(" ", concat(
          [
            "set -u;",
            "FIFO=/tmp/memtier-output.$$;",
            "mkfifo \"$FIFO\";",
            "UUID=$(cat /proc/sys/kernel/random/uuid);",
            "CLUSTER_NAME=${local.cluster_id}-loadgen;",
            "SERVICE_NAME=${local.cluster_id}-loadgen;",
            "TASK_ID=$UUID;",
            "if [ -n \"$${ECS_CONTAINER_METADATA_URI_V4:-}\" ]; then TASK_ARN=$( (wget -qO- \"$ECS_CONTAINER_METADATA_URI_V4/task\" 2>/dev/null || curl -fsS \"$ECS_CONTAINER_METADATA_URI_V4/task\" 2>/dev/null) | sed -n 's/.*\"TaskARN\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' ); if [ -n \"$TASK_ARN\" ]; then TASK_ID=$${TASK_ARN##*/}; fi; fi;",
            "MEMTIER_PID=;",
            "term() { if [ -n \"$MEMTIER_PID\" ]; then kill -INT \"$MEMTIER_PID\" 2>/dev/null || true; fi; };",
            "trap term TERM INT;",
            "awk -v cluster=\"$CLUSTER_NAME\" -v service=\"$SERVICE_NAME\" -v task=\"$TASK_ID\" 'BEGIN { RS = \"[\\r\\n]+\" } function esc(s) { gsub(/\\\\/, \"\\\\\\\\\", s); gsub(/\"/, \"\\\\\\\"\", s); return s } function emit(  i,json,ts) { if (n == 0) return; ts = sprintf(\"%.0f\", systime() * 1000); json = \"{\\\"_aws\\\":{\\\"Timestamp\\\":\" ts \",\\\"CloudWatchMetrics\\\":[{\\\"Namespace\\\":\\\"MemoryDB/LoadGenerator\\\",\\\"Dimensions\\\":[[\\\"ClusterName\\\",\\\"ServiceName\\\",\\\"TaskId\\\"]],\\\"Metrics\\\":[{\\\"Name\\\":\\\"ClientLatency\\\",\\\"Unit\\\":\\\"Milliseconds\\\",\\\"StorageResolution\\\":60}]}]},\\\"ClusterName\\\":\\\"\" esc(cluster) \"\\\",\\\"ServiceName\\\":\\\"\" esc(service) \"\\\",\\\"TaskId\\\":\\\"\" esc(task) \"\\\",\\\"ClientLatency\\\":[\"; for (i = 1; i <= n; i++) json = json (i > 1 ? \",\" : \"\") vals[i]; json = json \"]}\"; print json; fflush(); n = 0 } function capture(line,  tmp,lat) { if (index(line, \"msec latency\") == 0) return; tmp = line; if (index(tmp, \"(avg:\") > 0) { sub(/^.*\\(avg:[[:space:]]*/, \"\", tmp); sub(/\\)[[:space:]]*msec latency.*$/, \"\", tmp) } else { sub(/[[:space:]]*msec latency.*$/, \"\", tmp); sub(/^.*[^0-9.]/, \"\", tmp) } lat = tmp; gsub(/^[[:space:]]+|[[:space:]]+$/, \"\", lat); if (lat ~ /^[0-9]+(\\.[0-9]+)?$/) { vals[++n] = lat; if (n >= 100) emit() } } NF { print; capture($0); fflush() } END { emit() }' < \"$FIFO\" &",
            "FILTER_PID=$!;",
            "if command -v stdbuf >/dev/null 2>&1; then STDBUF=\"stdbuf -o0\"; else STDBUF=\"\"; fi;",
            "$STDBUF memtier_benchmark",
            "--server=${local.memorydb_endpoint}",
            "--port=${var.port}",
            "--threads=${var.loadgen_memtier_threads}",
            "--clients=${var.loadgen_memtier_clients}",
            "--pipeline=${var.loadgen_memtier_pipeline}",
            "--data-size=${var.loadgen_memtier_data_size}",
            "--ratio=${var.loadgen_memtier_ratio}",
            "--test-time=${local.memtier_test_time_seconds}",
            "--key-pattern=${var.loadgen_memtier_key_pattern}",
            "--key-prefix=$UUID-",
            "--key-maximum=${local.memtier_key_maximum}",
            "--hide-histogram"
          ],
          # Unconditional: MemoryDB has no non-cluster mode, so a client that
          # is not cluster-aware talks to one shard and silently measures a
          # fraction of the cluster.
          ["--cluster-mode"],
          var.tls_enabled ? ["--tls", "--tls-skip-verify"] : [],
          [
            "> \"$FIFO\" 2>&1 &",
            "MEMTIER_PID=$!;",
            "while true; do wait \"$MEMTIER_PID\"; STATUS=$?; if [ \"$STATUS\" -ge 128 ] && kill -0 \"$MEMTIER_PID\" 2>/dev/null; then continue; fi; break; done;",
            "wait \"$FILTER_PID\" 2>/dev/null || true;",
            "rm -f \"$FIFO\";",
            "exit \"$STATUS\""
          ]
        ))
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.loadgen.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "memtier"
        }
      }
    }
  ])

  tags = {
    Name = "${local.cluster_id}-loadgen"
  }
}

# ECS Service to run load generator tasks
resource "aws_ecs_service" "loadgen" {
  name            = "${local.cluster_id}-loadgen"
  cluster         = aws_ecs_cluster.loadgen.id
  task_definition = aws_ecs_task_definition.loadgen.arn
  desired_count   = var.loadgen_task_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.subnet_ids
    security_groups = [aws_security_group.loadgen.id]
    # Workaround only: enable when subnets have no NAT/egress for Docker Hub pulls.
    # Not recommended for normal use; prefer private subnets with NAT or ECR.
    assign_public_ip = var.loadgen_assign_public_ip
  }

  # Allow tasks to complete without being rescheduled
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0

  tags = {
    Name = "${local.cluster_id}-loadgen"
  }

  depends_on = [
    aws_memorydb_cluster.main,
    aws_cloudwatch_event_target.shutdown_scheduler,
    aws_lambda_permission.eventbridge_shutdown_scheduler
  ]
}


locals {
  # MemoryDB publishes exactly one cluster endpoint. There is no
  # primary/configuration split to branch on, because there is no
  # non-cluster mode to branch for.
  memorydb_endpoint = aws_memorydb_cluster.main.cluster_endpoint[0].address

  # Run effectively indefinitely when test time is 0.
  memtier_test_time_seconds = var.loadgen_memtier_test_time > 0 ? var.loadgen_memtier_test_time : 2147483647
  memtier_duration_label    = var.loadgen_memtier_test_time > 0 ? "${var.loadgen_memtier_test_time}s" : "until stopped"

  # ---------------------------------------------------------------------------
  # Usable memory (bytes) per MemoryDB node type.
  #
  # Source: AWS Price List API, service AmazonMemoryDB, us-east-1, the
  # `memory` attribute (advertised GiB) of every NodeUsage SKU, fetched
  # 2026-09-29. The Redis OSS and Valkey SKU of each node type carry the same
  # figure. Bytes = GiB x 2^30, rounded, with the source figure in a trailing
  # comment the way the source project did; the 85% fill factor below is
  # applied separately so this map stays auditable against the source.
  #
  # None of ElastiCache's figures were carried over: MemoryDB does not sell
  # most of those families, and where both products share a family the
  # usable memory differs because the two services reserve differently.
  #
  # An unlisted node_type fails the plan (the precondition in main.tf names
  # the supported set). The burstable prefix tuple in reporter/exporter.py
  # must cover every burstable family listed here;
  # tests/test_memorydb_node_catalog.py fails if it does not.
  # ---------------------------------------------------------------------------
  _node_memory_bytes = {
    # T4g family (burstable)
    "db.t4g.small"  = 1471026299 # 1.37 GiB
    "db.t4g.medium" = 3317862236 # 3.09 GiB
    # R6g family
    "db.r6g.large"    = 14033805640  # 13.07 GiB
    "db.r6g.xlarge"   = 28260884808  # 26.32 GiB
    "db.r6g.2xlarge"  = 56715043144  # 52.82 GiB
    "db.r6g.4xlarge"  = 113612622397 # 105.81 GiB
    "db.r6g.8xlarge"  = 225002599219 # 209.55 GiB
    "db.r6g.12xlarge" = 341202939412 # 317.77 GiB
    "db.r6g.16xlarge" = 449994461020 # 419.09 GiB
    # R6gd family (data tiering; RAM only, the SSD tier comes on top)
    "db.r6gd.xlarge"  = 28260884808  # 26.32 GiB
    "db.r6gd.2xlarge" = 56715043144  # 52.82 GiB
    "db.r6gd.4xlarge" = 113612622397 # 105.81 GiB
    "db.r6gd.8xlarge" = 225002599219 # 209.55 GiB
    # R7g family
    "db.r7g.large"    = 14033805640  # 13.07 GiB
    "db.r7g.xlarge"   = 28260884808  # 26.32 GiB
    "db.r7g.2xlarge"  = 56715043144  # 52.82 GiB
    "db.r7g.4xlarge"  = 113612622397 # 105.81 GiB
    "db.r7g.8xlarge"  = 225002599219 # 209.55 GiB
    "db.r7g.12xlarge" = 341202939412 # 317.77 GiB
    "db.r7g.16xlarge" = 449994461020 # 419.09 GiB
  }

  # Live on-demand hourly price for this run's exact node_type / engine_type
  # / aws_region, from the AWS Price List API (see data.external.node_price
  # above). Region-qualified rather than a hardcoded single-region table,
  # which would silently mislabel every run outside that one region.
  #
  # count-indexed because the data source is gated by var.enable_price_lookup;
  # lookup()s below default missing keys instead of erroring, since a plain
  # `.result.reason` index fails plan/apply on the (expected) success path,
  # where the script's JSON has no "reason" key at all.
  _node_price_result = (
    var.enable_price_lookup
    ? data.external.node_price[0].result
    : { hourly_usd = "", source = "disabled" }
  )

  # "" (not a number) means the live lookup failed or was disabled -- see
  # _node_hourly_usd_reason. Never falls back to a stale guess; downstream
  # consumers must treat "" as unknown, not zero.
  _node_hourly_usd        = local._node_price_result.hourly_usd != "" ? tonumber(local._node_price_result.hourly_usd) : null
  _node_hourly_usd_source = local._node_price_result.source
  _node_hourly_usd_reason = lookup(local._node_price_result, "reason", "")

  # 85% fill factor — cache stays warm without hitting eviction pressure
  _fill_factor = 0.85

  # Advertised bytes for this run. Unknown node types must fail at plan time;
  # silent fallback would corrupt the benchmark keyspace.
  _advertised_bytes = local._node_memory_bytes[var.node_type]

  # key-maximum: how many data_size-byte values fit at the target fill factor.
  # Each key carries ~70 bytes of engine overhead on top of the value.
  _key_overhead_bytes = 70
  _bytes_per_key      = var.loadgen_memtier_data_size + local._key_overhead_bytes

  # Capacity scales with primary shards. Replicas mirror their primary, so
  # they add read capacity but no writable keyspace -- and MemoryDB is always
  # sharded, so this is num_shards with no branch.
  _writable_shard_count = var.num_shards
  _target_fill_bytes    = local._advertised_bytes * local._writable_shard_count * local._fill_factor
  _target_total_keys    = floor(local._target_fill_bytes / local._bytes_per_key)
  _target_keys_per_task = max(1, floor(local._target_total_keys / var.loadgen_task_count))

  # If the user explicitly set key-maximum, honour it; otherwise auto-compute.
  memtier_key_maximum = var.loadgen_memtier_key_maximum > 0 ? var.loadgen_memtier_key_maximum : (
    local._target_keys_per_task
  )
}
