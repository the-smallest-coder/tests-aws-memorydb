terraform {
  required_version = ">= 1.0"

  # Floor versions only, never an upper bound: `terraform init` takes the
  # newest provider release, and running against it is part of what this
  # test project tests. A release that breaks the configuration is a finding
  # to fix here, not something to hold off with a constraint.
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.10"
    }
    external = {
      source  = "hashicorp/external"
      version = ">= 2.3"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Project   = var.project_name
        ManagedBy = "Terraform"
        Purpose   = "MemoryDB Performance Testing"
      },
      var.tags
    )
  }
}
