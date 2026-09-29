variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name for resource naming and tagging"
  type        = string
  default     = "memorydb-perf-test"
}


# VPC and Networking
variable "vpc_id" {
  description = "ID of existing VPC where MemoryDB will be deployed"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for the MemoryDB subnet group"
  type        = list(string)
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to connect to MemoryDB (defaults to VPC CIDR if empty)"
  type        = list(string)
  default     = []
}

# MemoryDB Configuration
variable "engine_type" {
  description = "Engine: redis or valkey. Defaults to valkey because the server-side latency metrics this project reports (SuccessfulReadRequestLatency, SuccessfulWriteRequestLatency) require Valkey 7.2+ and do not exist on Redis OSS."
  type        = string
  default     = "valkey"

  validation {
    condition     = contains(["redis", "valkey"], var.engine_type)
    error_message = "Engine type must be either 'redis' or 'valkey'."
  }
}

variable "engine_version" {
  description = "Engine version (e.g., 7.2 for Valkey). Below 7.2 the server-side latency metrics are absent."
  type        = string
  default     = "7.2"
}

variable "node_type" {
  description = "MemoryDB node instance type (db.*). There is no db.t4g.micro; db.t4g.small is the smallest MemoryDB offers."
  type        = string
  default     = "db.t4g.small"
}

variable "enable_price_lookup" {
  description = <<-EOT
    Look up this run's live hourly node price via scripts/fetch_memorydb_price.sh
    (requires bash, aws, and jq on whatever machine runs Terraform). The script itself
    always exits 0, but Terraform launching it at all can still fail the plan if bash
    isn't on PATH -- that's a `data` block, so it's re-evaluated on every plan,
    including `terraform destroy`. Set to false (e.g. `-var enable_price_lookup=false`)
    as an escape hatch if that ever blocks a destroy; node_hourly_usd_source in
    cluster_details.json becomes "disabled" instead of erroring.
  EOT
  type        = bool
  default     = true
}

# Topology. MemoryDB is always sharded -- one shard is still a cluster.
variable "num_shards" {
  description = "Number of shards. MemoryDB has no non-cluster mode, so this is always at least 1."
  type        = number
  default     = 1

  validation {
    condition     = var.num_shards >= 1 && var.num_shards <= 500
    error_message = "Number of shards must be between 1 and 500."
  }
}

variable "num_replicas_per_shard" {
  description = <<-EOT
    Replicas per shard. 0 -- a single-node cluster -- so runs stay comparable
    with the single-node ElastiCache runs this project is measured against.
    This deliberately overrides MemoryDB's own API default of 1. Durability
    does not depend on it: MemoryDB commits writes to its multi-AZ
    transaction log either way. Nodes billed = num_shards x
    (num_replicas_per_shard + 1). With 0, the replication metrics
    (ReplicationLag, ReplicationBytes, MaxReplicationThroughput,
    ReplicationDelayedWriteCommands) have nothing to report, and the export
    contract does not require them.
  EOT
  type        = number
  default     = 0

  validation {
    condition     = var.num_replicas_per_shard >= 0 && var.num_replicas_per_shard <= 5
    error_message = "Replicas per shard must be between 0 and 5."
  }
}

variable "acl_name" {
  description = "MemoryDB ACL to attach. Every cluster must name one; open-access is the immutable account-wide ACL used for benchmarking, and is mandatory when tls_enabled is false."
  type        = string
  default     = "open-access"
}

variable "tls_enabled" {
  description = "Encryption in transit. MemoryDB defaults this to true; the load generator passes --tls to match. Disabling it forces acl_name to open-access."
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for encryption at rest. Empty uses the AWS-owned key. Encryption at rest itself is always on and cannot be disabled."
  type        = string
  default     = ""
}

variable "data_tiering" {
  description = "Enable data tiering (SSD-backed). Only valid on db.r6gd.* node types, fixed at create time, and adds the Tier dimension plus the disk metric family to CloudWatch."
  type        = bool
  default     = false
}

# Performance and Configuration
variable "port" {
  description = "Port number for MemoryDB"
  type        = number
  default     = 6379
}

variable "parameter_group_family" {
  description = "Parameter group family (auto-determined from engine and version if not set)"
  type        = string
  default     = ""
}

variable "parameter_group_settings" {
  description = "Custom parameter group settings as key-value pairs"
  type        = map(string)
  default     = {}
}

variable "snapshot_retention_limit" {
  description = "Number of days to retain snapshots (0 to disable)"
  type        = number
  default     = 0
}

variable "snapshot_window" {
  description = "Daily time range for snapshots (HH:MM-HH:MM in UTC)"
  type        = string
  default     = "03:00-05:00"
}

variable "maintenance_window" {
  description = "Weekly time range for maintenance (ddd:HH:MM-ddd:HH:MM in UTC)"
  type        = string
  default     = "sun:05:00-sun:07:00"
}

# CloudWatch
variable "cloudwatch_log_retention_days" {
  description = "Number of days to retain CloudWatch logs"
  type        = number
  default     = 7
}

variable "enable_cloudwatch_dashboard" {
  description = "Create CloudWatch dashboard for monitoring"
  type        = bool
  default     = true
}

# ECS observability
variable "ecs_container_insights_mode" {
  description = "ECS Container Insights mode (enhanced required for task-level metrics; may increase cost)"
  type        = string
  default     = "enhanced"

  validation {
    condition     = contains(["enabled", "disabled", "enhanced"], var.ecs_container_insights_mode)
    error_message = "ecs_container_insights_mode must be one of: enabled, disabled, enhanced."
  }
}

# Tags
variable "tags" {
  description = "Additional tags for resources"
  type        = map(string)
  default     = {}
}

# Load Generator Configuration
variable "loadgen_task_count" {
  description = "Number of ECS tasks running memtier_benchmark (scale factor)"
  type        = number
  default     = 1

  validation {
    condition     = var.loadgen_task_count >= 1 && var.loadgen_task_count <= 100
    error_message = "Load generator task count must be between 1 and 100."
  }
}

variable "loadgen_image" {
  description = "memtier_benchmark container image. The one pinned dependency in this project: MemoryDB results are compared against the ElastiCache repo (tests-aws-elasticache-redis), so both must measure with the same memtier build. Keep this digest identical to that repo's loadgen_image default and bump both together (tests/test_reporter_dependencies.py compares them when the sibling repo is checked out)."
  type        = string
  # Manifest-list (multi-arch) digest, as in the ElastiCache repo, fetched
  # there 2026-09-07 from redislabs/memtier_benchmark:latest.
  default = "redislabs/memtier_benchmark@sha256:5f15b74f657fd30ee73453af9caa1781de1614f4d934d46feee711dc19b758af"
}

variable "reporter_image" {
  description = "Base image for the reporter ECS task. Deliberately a floating tag (newest Python): it only renders reports and does not affect what is measured. Kept the same as reporter/Dockerfile's FROM line (tests/test_reporter_dependencies.py checks it), which this task definition does not use today but should track the same base."
  type        = string
  default     = "python:slim"
}

variable "loadgen_cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU, 512 = 0.5 vCPU, 1024 = 1 vCPU)"
  type        = number
  default     = 256
}

variable "loadgen_memory" {
  description = "Fargate memory in MB (minimum 512)"
  type        = number
  default     = 512

  validation {
    condition     = var.loadgen_memory >= 512
    error_message = "Fargate requires minimum 512 MB memory."
  }
}

variable "loadgen_assign_public_ip" {
  description = "Assign a public IP to the load generator tasks (workaround for no NAT/egress; not recommended for normal use)"
  type        = bool
  default     = false
}

# memtier_benchmark Configuration
variable "loadgen_memtier_threads" {
  description = "Number of threads per memtier task"
  type        = number
  default     = 1
}

variable "loadgen_memtier_clients" {
  description = "Number of clients per thread"
  type        = number
  default     = 1
}

variable "loadgen_memtier_pipeline" {
  description = "Pipeline depth (number of concurrent requests)"
  type        = number
  default     = 1
}

variable "loadgen_memtier_data_size" {
  description = "Data size in bytes for SET operations"
  type        = number
  default     = 32
}

variable "loadgen_memtier_ratio" {
  description = "SET:GET ratio (e.g., '1:10' means 1 SET for every 10 GETs)"
  type        = string
  default     = "1:10"
}

variable "loadgen_memtier_test_time" {
  description = "Test duration in seconds (0 = run until ECS stops the task)"
  type        = number
  default     = 0
}

variable "loadgen_memtier_key_pattern" {
  description = "memtier key access pattern, including mixed patterns such as S:R"
  type        = string
  default     = "R:R"
}

variable "loadgen_memtier_key_maximum" {
  description = "Maximum key count per load generator task. 0 (default) auto-computes total target keys from writable cache memory (~85% of primary/shard capacity), then divides by loadgen_task_count. Set >0 to use a specific per-task value. Each task still gets a unique key prefix so keyspaces do not overlap."
  type        = number
  default     = 0

  validation {
    condition     = var.loadgen_memtier_key_maximum >= 0 && floor(var.loadgen_memtier_key_maximum) == var.loadgen_memtier_key_maximum
    error_message = "loadgen_memtier_key_maximum must be a non-negative integer (0 or greater)."
  }
}

# Metrics Export Configuration
variable "metrics_export_s3_bucket" {
  description = "S3 bucket for exporting metrics and logs (REQUIRED)"
  type        = string

  validation {
    condition     = length(var.metrics_export_s3_bucket) > 0
    error_message = "metrics_export_s3_bucket is required. Create an S3 bucket before running terraform apply."
  }
}

variable "metrics_export_s3_prefix" {
  description = "S3 key prefix for exported data"
  type        = string
  default     = "exports/"
}

variable "test_duration_minutes" {
  description = "Test duration in minutes before auto-shutdown (default: 60)"
  type        = number
  default     = 60

  validation {
    condition     = var.test_duration_minutes >= 1 && var.test_duration_minutes <= 1440
    error_message = "Test duration must be between 1 and 1440 minutes (24 hours)."
  }
}

# Email Notification Configuration
variable "notification_email" {
  description = "Email address for shutdown completion notifications (optional; leave empty to disable)"
  type        = string
  default     = ""
}

variable "notification_ses_identity_arn" {
  description = "SES verified domain identity ARN for sending notifications (required when notification_email is set)"
  type        = string
  default     = ""

  validation {
    condition     = (var.notification_email == "" && var.notification_ses_identity_arn == "") || (var.notification_email != "" && var.notification_ses_identity_arn != "")
    error_message = "Both notification_email and notification_ses_identity_arn must be set together, or both left empty."
  }
}

variable "run_id_discriminator" {
  description = "Short per-run uniqueness token appended to cluster_id and run_folder. Lowercase alphanumeric, max 8 chars. Empty string preserves legacy timestamp-only identity."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^[a-z0-9]{0,8}$", var.run_id_discriminator))
    error_message = "run_id_discriminator must be 0-8 lowercase alphanumeric characters ([a-z0-9])."
  }
}
