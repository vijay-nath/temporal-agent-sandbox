# Reference production infrastructure (not applied by the local stack). Terraform provisions
# the substrate — network, cluster, secrets; in-cluster workloads are managed by GitOps.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" }
  }
  # backend "s3" { ... }   # remote state + locking
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "temporal-agent-sandbox"
}

# Network: API in public subnets behind an ALB; workers + sandboxes in PRIVATE subnets
# (never internet-facing — enforces the control/data-plane split).
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name            = var.project
  cidr            = "10.0.0.0/16"
  azs             = ["${var.region}a", "${var.region}b", "${var.region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway = true
  single_nat_gateway = true
}

# Secrets: API/worker config (NEVER the sandbox) via Secrets Manager + the CSI driver.
resource "aws_secretsmanager_secret" "app" {
  name = "${var.project}/app"
}

# Untrusted code outputs / claim-check artifacts (prod) — private, encrypted.
resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.project}-artifacts"
}
