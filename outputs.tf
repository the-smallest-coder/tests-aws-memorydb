output "memorydb_cluster_name" {
  description = "MemoryDB cluster name"
  value       = aws_memorydb_cluster.main.name
}

output "memorydb_cluster_arn" {
  description = "ARN of the MemoryDB cluster"
  value       = aws_memorydb_cluster.main.arn
}

# MemoryDB publishes a single cluster endpoint. There is no primary/reader
# endpoint pair and no configuration-endpoint variant: the cluster is always
# sharded, so clients discover primaries and replicas through cluster topology
# and every client must be cluster-aware.
output "memorydb_endpoint" {
  description = "Cluster endpoint address for MemoryDB"
  value       = aws_memorydb_cluster.main.cluster_endpoint[0].address
}

output "memorydb_port" {
  description = "Port number for MemoryDB"
  value       = aws_memorydb_cluster.main.cluster_endpoint[0].port
}

output "memorydb_connection_string" {
  description = "Connection string for Valkey/Redis cluster-aware clients"
  value = format("%s:%d",
    aws_memorydb_cluster.main.cluster_endpoint[0].address,
  aws_memorydb_cluster.main.cluster_endpoint[0].port)
}

output "memorydb_tls_enabled" {
  description = "Whether the cluster requires TLS. Defaults to true on MemoryDB, so clients must pass --tls."
  value       = aws_memorydb_cluster.main.tls_enabled
}

output "memorydb_acl_name" {
  description = "ACL attached to the cluster"
  value       = aws_memorydb_cluster.main.acl_name
}

output "security_group_id" {
  description = "ID of the MemoryDB security group"
  value       = aws_security_group.memorydb.id
}

output "subnet_group_name" {
  description = "Name of the MemoryDB subnet group"
  value       = aws_memorydb_subnet_group.main.name
}

# No cloudwatch_log_group_name output: MemoryDB has no log-delivery
# configuration, so there is no slow-log or engine-log group to point at.

output "cloudwatch_dashboard_url" {
  description = "URL to CloudWatch dashboard"
  value = var.enable_cloudwatch_dashboard ? format(
    "https://console.aws.amazon.com/cloudwatch/home?region=%s#dashboards:name=%s",
    var.aws_region,
    aws_cloudwatch_dashboard.memorydb[0].dashboard_name
  ) : null
}

output "configuration_summary" {
  description = "Summary of applied configuration"
  value = {
    engine                 = var.engine_type
    engine_version         = var.engine_version
    node_type              = var.node_type
    num_shards             = var.num_shards
    num_replicas_per_shard = var.num_replicas_per_shard
    node_count             = local.memorydb_node_count
    acl_name               = var.acl_name
    tls_enabled            = var.tls_enabled
    data_tiering           = var.data_tiering
    # Not reported because MemoryDB does not make them optional: encryption at
    # rest is always on, the cluster is always multi-AZ with automatic
    # failover, and AZ placement cannot be requested. Placement that actually
    # happened is recorded per node in cluster_details.json instead.
  }
}

output "run_timestamp" {
  description = "Compact 24-hour run timestamp used for IDs"
  value       = local.run_id_full
}

output "run_folder" {
  description = "S3 result folder for this run"
  value       = local.run_folder
}

# Load Generator Outputs
output "loadgen_cluster_name" {
  description = "ECS cluster name for load generators"
  value       = aws_ecs_cluster.loadgen.name
}

output "loadgen_service_name" {
  description = "ECS service name for load generators"
  value       = aws_ecs_service.loadgen.name
}

output "loadgen_log_group_name" {
  description = "CloudWatch log group for load generator output"
  value       = aws_cloudwatch_log_group.loadgen.name
}

output "loadgen_security_group_id" {
  description = "Security group ID for load generator tasks"
  value       = aws_security_group.loadgen.id
}

output "loadgen_configuration" {
  description = "Summary of load generator configuration"
  value = {
    task_count       = var.loadgen_task_count
    cpu              = var.loadgen_cpu
    memory           = var.loadgen_memory
    threads          = var.loadgen_memtier_threads
    clients          = var.loadgen_memtier_clients
    pipeline         = var.loadgen_memtier_pipeline
    data_size        = var.loadgen_memtier_data_size
    ratio            = var.loadgen_memtier_ratio
    test_time        = var.loadgen_memtier_test_time
    key_pattern      = var.loadgen_memtier_key_pattern
    key_maximum      = var.loadgen_memtier_key_maximum
    key_maximum_mode = "per_task"
    task_partitioned = true
  }
}

# Shutdown and Export Outputs
output "scheduled_shutdown_minutes" {
  description = "Minutes until auto-shutdown triggers"
  value       = var.test_duration_minutes
}

output "metrics_export_location" {
  description = "S3 location where metrics and logs will be exported"
  value       = "s3://${var.metrics_export_s3_bucket}/${var.metrics_export_s3_prefix}"
}

output "shutdown_lambda_name" {
  description = "Lambda function name for shutdown orchestration"
  value       = aws_lambda_function.shutdown.function_name
}

output "run_id_discriminator" {
  description = "Per-run uniqueness token used in cluster_id and run_folder (empty if legacy timestamp-only)."
  value       = var.run_id_discriminator
}
