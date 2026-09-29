# ---------------------------------------------------------------------------
# cluster_details.json — uploaded to S3 immediately after terraform apply.
#
# This file is the authoritative record of what was tested: every parameter
# that could explain a performance difference between two runs.  Because the
# cluster is destroyed after each test, the JSON must be written before the
# test begins (i.e. at apply time, not at destroy time).
#
# S3 path: {metrics_export_s3_prefix}{run_folder}/cluster_details.json
# The same run_folder is used by the shutdown Lambda for metrics/logs, so
# all artefacts for one test run live in a single deterministic folder.
# ---------------------------------------------------------------------------

resource "aws_s3_object" "cluster_details" {
  bucket = var.metrics_export_s3_bucket
  key    = "${var.metrics_export_s3_prefix}${local.run_folder}/cluster_details.json"

  content = jsonencode({
    # ----- Run metadata -----
    run = {
      run_id               = local.run_id_full
      run_folder           = local.run_folder
      cluster_id           = local.cluster_id
      aws_region           = var.aws_region
      run_id_discriminator = var.run_id_discriminator
      # Run provenance. git_sha.sh always exits 0; "unknown"
      # means it couldn't determine one, not that provenance is unavailable
      # by design -- see the exit-condition table (git_sha == "unknown" at
      # apply from a git repo is a regression, not an expected state).
      git_sha             = data.external.git_sha.result.sha
      git_dirty           = data.external.git_sha.result.dirty
      terraform_workspace = terraform.workspace
    }

    # ----- MemoryDB configuration (as applied) -----
    memorydb = {
      engine                    = var.engine_type
      engine_version_configured = var.engine_version
      # The requested version can be a loose "7.2"; this is the patch level
      # that actually came up. engine_version_configured is left untouched
      # above -- the two carry different information and neither replaces
      # the other.
      engine_patch_version = aws_memorydb_cluster.main.engine_patch_version
      node_type            = var.node_type
      # Direct index, not lookup(..., 0): an unknown node type must fail the
      # apply, not silently record a zero-byte node and corrupt every
      # keyspace and cost figure derived from it. main.tf carries the
      # precondition that names the supported set.
      node_memory_bytes      = local._node_memory_bytes[var.node_type]
      node_hourly_usd        = local._node_hourly_usd
      node_hourly_usd_source = local._node_hourly_usd_source
      node_hourly_usd_reason = local._node_hourly_usd_reason

      # Topology. MemoryDB is always sharded, so there is no cluster-mode
      # flag and no non-cluster branch. node_count is what gets billed.
      num_shards             = var.num_shards
      num_replicas_per_shard = var.num_replicas_per_shard
      node_count             = local.memorydb_node_count

      # Security. Encryption at rest and multi-AZ are unconditional on
      # MemoryDB and therefore not recorded as choices; kms_key_arn records
      # whether a customer-managed key replaced the AWS-owned default.
      acl_name     = var.acl_name
      tls_enabled  = var.tls_enabled
      kms_key_arn  = var.kms_key_arn
      data_tiering = var.data_tiering

      port                     = var.port
      parameter_group_name     = length(var.parameter_group_settings) > 0 ? aws_memorydb_parameter_group.main[0].name : local.default_parameter_group_name
      parameter_group_family   = local.parameter_group_family
      parameter_group_settings = var.parameter_group_settings
      snapshot_retention_limit = var.snapshot_retention_limit
      maintenance_window       = var.maintenance_window

      # No cloudwatch_log_group: MemoryDB has no log-delivery configuration,
      # so there is no slow-log or engine-log destination to record.

      # Live endpoint (resolved after the cluster is created). MemoryDB
      # publishes one cluster endpoint -- no primary/reader/configuration
      # trio -- because every client is cluster-aware by necessity.
      cluster_endpoint = aws_memorydb_cluster.main.cluster_endpoint[0].address
      cluster_arn      = aws_memorydb_cluster.main.arn
    }

    # ----- Observed placement -----
    # MemoryDB has no availability-zone argument: placement is chosen by AWS
    # and can only be observed after the fact. Recording it per node is what
    # replaces the AZ pinning the source project used for reproducibility --
    # two runs of the same config stay *checkable* for comparability even
    # though placement is not *controllable*.
    placement = {
      shards = [
        for shard in aws_memorydb_cluster.main.shards : {
          name      = shard.name
          num_nodes = shard.num_nodes
          slots     = shard.slots
          nodes = [
            for node in shard.nodes : {
              name              = node.name
              availability_zone = node.availability_zone
              endpoint_address  = try(node.endpoint[0].address, null)
            }
          ]
        }
      ]
      availability_zones = distinct(sort(flatten([
        for shard in aws_memorydb_cluster.main.shards : [
          for node in shard.nodes : node.availability_zone
        ]
      ])))
    }

    # ----- memtier_benchmark configuration -----
    memtier = {
      task_count        = var.loadgen_task_count
      threads           = var.loadgen_memtier_threads
      clients           = var.loadgen_memtier_clients
      pipeline          = var.loadgen_memtier_pipeline
      data_size_bytes   = var.loadgen_memtier_data_size
      ratio             = var.loadgen_memtier_ratio
      key_pattern       = var.loadgen_memtier_key_pattern
      key_maximum       = local.memtier_key_maximum
      key_maximum_total = local.memtier_key_maximum * var.loadgen_task_count
      key_maximum_auto  = var.loadgen_memtier_key_maximum == 0
      auto_sizing = {
        writable_shard_count = local._writable_shard_count
        target_fill_bytes    = local._target_fill_bytes
        target_total_keys    = local._target_total_keys
      }
      test_time_seconds = local.memtier_test_time_seconds
      duration_label    = local.memtier_duration_label
      tls               = var.tls_enabled
      # Always true and not a variable: MemoryDB has no non-cluster mode, so
      # memtier always runs with --cluster-mode. Recorded so a reader of this
      # file does not have to know that to interpret the run.
      cluster_mode = true
    }

    # ----- ECS load generator resources -----
    ecs = {
      cluster_name            = local.loadgen_cluster_name
      service_name            = local.loadgen_service_name
      fargate_cpu             = var.loadgen_cpu
      fargate_memory          = var.loadgen_memory
      container_insights_mode = var.ecs_container_insights_mode
      loadgen_image           = var.loadgen_image
    }

    # ----- Reporter provenance (WP3) -----
    # Only the base image is known at apply time; the packages actually
    # installed (pip freeze, reporter.tf) are runtime-only and travel
    # through report_status.json / summary.meta.reporter_packages instead.
    reporter = {
      image = var.reporter_image
    }

    # ----- Node type memory reference table (all known types) -----
    node_memory_table = local._node_memory_bytes
  })

  content_type = "application/json"

  # Overwrite if a re-apply happens for the same run (edge case: apply failed
  # and was re-run within the same time_static tick).
  etag = md5(jsonencode({
    cluster_id = local.cluster_id
    node_type  = var.node_type
  }))

  tags = {
    Name      = "${local.cluster_id}-cluster-details"
    RunFolder = local.run_folder
  }

  depends_on = [aws_memorydb_cluster.main]
}
