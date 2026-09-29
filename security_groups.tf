data "aws_vpc" "selected" {
  id = var.vpc_id
}

resource "aws_security_group" "memorydb" {
  name_prefix = "${local.cluster_id}-"
  description = "Security group for MemoryDB ${var.engine_type} cluster"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${local.cluster_id}-memorydb"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "memorydb_access" {
  security_group_id = aws_security_group.memorydb.id
  description       = "Allow ${var.engine_type} access from VPC"

  from_port   = var.port
  to_port     = var.port
  ip_protocol = "tcp"

  # Allow from specified CIDR blocks or entire VPC if not specified
  cidr_ipv4 = length(var.allowed_cidr_blocks) > 0 ? var.allowed_cidr_blocks[0] : data.aws_vpc.selected.cidr_block
}

# Additional ingress rules for multiple CIDR blocks if specified
resource "aws_vpc_security_group_ingress_rule" "memorydb_access_additional" {
  count = length(var.allowed_cidr_blocks) > 1 ? length(var.allowed_cidr_blocks) - 1 : 0

  security_group_id = aws_security_group.memorydb.id
  description       = "Allow ${var.engine_type} access from additional CIDR ${count.index + 1}"

  from_port   = var.port
  to_port     = var.port
  ip_protocol = "tcp"
  cidr_ipv4   = var.allowed_cidr_blocks[count.index + 1]
}

resource "aws_vpc_security_group_egress_rule" "memorydb_egress" {
  security_group_id = aws_security_group.memorydb.id
  description       = "Allow all outbound traffic"

  ip_protocol = "-1"
  cidr_ipv4   = "0.0.0.0/0"
}

# Security group for ECS load generator tasks
resource "aws_security_group" "loadgen" {
  name_prefix = "${local.cluster_id}-loadgen-"
  description = "Security group for ECS load generator tasks"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${local.cluster_id}-loadgen"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Allow all outbound traffic from load generator (to reach MemoryDB)
resource "aws_vpc_security_group_egress_rule" "loadgen_egress" {
  security_group_id = aws_security_group.loadgen.id
  description       = "Allow all outbound traffic"

  ip_protocol = "-1"
  cidr_ipv4   = "0.0.0.0/0"
}

# Allow ECS load generator tasks to connect to MemoryDB
resource "aws_vpc_security_group_ingress_rule" "memorydb_from_loadgen" {
  security_group_id            = aws_security_group.memorydb.id
  description                  = "Allow ${var.engine_type} access from load generator tasks"
  referenced_security_group_id = aws_security_group.loadgen.id

  from_port   = var.port
  to_port     = var.port
  ip_protocol = "tcp"
}
