resource "time_static" "run_id" {}

# Provenance of the git commit this `terraform apply` actually ran from.
# Modeled on data.external.node_price in ecs.tf: the script always exits 0
# ({"sha":"unknown","dirty":"unknown"} on any failure), so a missing git or
# a non-repo checkout never blocks apply or destroy. See scripts/git_sha.sh.
data "external" "git_sha" {
  program = ["bash", "${path.module}/scripts/git_sha.sh"]

  query = {}
}

locals {
  # MemoryDB parameter-group families are prefixed and use an underscore
  # ("memorydb_valkey7"), while the *default parameter group* AWS ships for
  # that family uses a hyphen ("default.memorydb-valkey7"). They are not the
  # same string and are not interchangeable; WP0 step 2 confirms both against
  # describe-parameter-groups.
  parameter_group_family = var.parameter_group_family != "" ? var.parameter_group_family : (
    "memorydb_${var.engine_type}${split(".", var.engine_version)[0]}"
  )
  default_parameter_group_name = "default.${replace(local.parameter_group_family, "_", "-")}"

  # Compact run timestamp and suffix keep IDs within MemoryDB length limits.
  run_id_full   = formatdate("YYYYMMDDHHmmss", time_static.run_id.rfc3339)
  run_id_suffix = substr(local.run_id_full, length(local.run_id_full) - 8, 8)

  # Optional per-run uniqueness token. Empty preserves legacy timestamp-only IDs.
  run_disc_suffix = var.run_id_discriminator != "" ? "-${var.run_id_discriminator}" : ""

  # Human-readable folder name shared by all artefacts for this run:
  # Terraform writes cluster_details.json here at apply time; the Lambda
  # uses the same name for metrics/logs so everything lands in one folder.
  run_folder = "${formatdate("YYYYMMDD-HHmmss", time_static.run_id.rfc3339)}${local.run_disc_suffix}"

  # Cluster identifier (run-scoped)
  cluster_id = "${var.project_name}-${var.engine_type}-${local.run_id_suffix}${local.run_disc_suffix}"

  # MemoryDB bills every node, replicas included, and defaults to one replica
  # per shard. The node count that matters for cost and for per-node metric
  # dimensions is therefore shards x (replicas + 1), never num_shards alone.
  memorydb_node_count = var.num_shards * (var.num_replicas_per_shard + 1)

  # Common tags
  common_tags = {
    Name   = local.cluster_id
    Engine = var.engine_type
    Shards = tostring(var.num_shards)
  }
}

# Subnet group for MemoryDB
resource "aws_memorydb_subnet_group" "main" {
  name       = "${local.cluster_id}-subnet-group"
  subnet_ids = var.subnet_ids

  tags = {
    Name = "${local.cluster_id}-subnet-group"
  }
}

# Parameter group for custom settings
resource "aws_memorydb_parameter_group" "main" {
  count = length(var.parameter_group_settings) > 0 ? 1 : 0

  name   = "${local.cluster_id}-params"
  family = local.parameter_group_family

  dynamic "parameter" {
    for_each = var.parameter_group_settings
    content {
      name  = parameter.key
      value = parameter.value
    }
  }

  tags = {
    Name = "${local.cluster_id}-params"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# MemoryDB cluster under test.
#
# Hand-rolled rather than built on terraform-aws-modules/memory-db/aws. Three
# resources with no conditional topology is less surface than the module's
# variable contract, the module adds an ACL/user layer this project does not
# use (open-access is the benchmarking default), and a benchmark harness wants
# every argument that shapes the result visible in one file rather than
# resolved inside a dependency. The cost is that MemoryDB argument changes are
# ours to track; that is accepted.
#
# Differences from the ElastiCache original, all forced by the service:
#   - always sharded, so there is no non-cluster branch
#   - always multi-AZ and always encrypted at rest, so no toggles
#   - TLS defaults on and an ACL is mandatory
#   - no log_delivery_configuration and no AZ placement argument
resource "aws_memorydb_cluster" "main" {
  name        = local.cluster_id
  description = "MemoryDB ${var.engine_type} cluster for performance testing"

  # Engine configuration
  engine         = var.engine_type
  engine_version = var.engine_version
  port           = var.port
  node_type      = var.node_type

  # Topology. MemoryDB has no non-cluster mode; a single shard is still a
  # sharded cluster and clients still have to be cluster-aware.
  num_shards             = var.num_shards
  num_replicas_per_shard = var.num_replicas_per_shard

  # Parameter group
  parameter_group_name = length(var.parameter_group_settings) > 0 ? aws_memorydb_parameter_group.main[0].name : local.default_parameter_group_name

  # Network configuration
  subnet_group_name  = aws_memorydb_subnet_group.main.name
  security_group_ids = [aws_security_group.memorydb.id]

  # Security. Encryption at rest is always on and has no argument; kms_key_arn
  # only swaps the AWS-owned key for a customer-managed one. TLS defaults to
  # true, which is why the load generator always passes --tls.
  acl_name    = var.acl_name
  tls_enabled = var.tls_enabled
  kms_key_arn = var.kms_key_arn != "" ? var.kms_key_arn : null

  # Data tiering is fixed at create time and only valid on r6gd nodes. It adds
  # the Tier dimension to CloudWatch and the BytesReadFromDisk metric family,
  # which the exporter contract branches on.
  data_tiering = var.data_tiering

  # Backup and maintenance
  snapshot_retention_limit = var.snapshot_retention_limit
  snapshot_window          = var.snapshot_retention_limit > 0 ? var.snapshot_window : null
  maintenance_window       = var.maintenance_window

  # Auto minor version upgrade
  auto_minor_version_upgrade = true

  tags = local.common_tags

  lifecycle {
    ignore_changes = [engine_version]

    precondition {
      condition     = length(local.cluster_id) <= 40
      error_message = "cluster_id '${local.cluster_id}' exceeds MemoryDB's 40-character cluster-name limit. Shorten project_name or run_id_discriminator."
    }

    # local._node_memory_bytes[var.node_type] in ecs.tf raises a bare
    # "Invalid index" for any unlisted node_type -- deliberately, since a
    # silent fallback would corrupt the benchmark keyspace (see the comment
    # there). This precondition runs alongside that raw error and names the
    # actual supported set so the failure is actionable.
    precondition {
      condition     = contains(keys(local._node_memory_bytes), var.node_type)
      error_message = "node_type '${var.node_type}' is not in the supported catalog. Supported types: ${join(", ", sort(keys(local._node_memory_bytes)))}."
    }

    # Data tiering exists only on the r6gd family. Setting it on anything else
    # is rejected by the API at create time, several minutes into an apply.
    precondition {
      condition     = !var.data_tiering || startswith(var.node_type, "db.r6gd.")
      error_message = "data_tiering is only supported on db.r6gd.* node types; node_type is '${var.node_type}'."
    }
  }
}
