# Temporal in production. Prefer Temporal Cloud: self-hosting means operating a sharded,
# stateful cluster plus its datastore and Elasticsearch. Cloud also gives per-namespace
# isolation and mTLS; self-host only for data-residency or air-gap needs.
#
# Workers connect over mTLS; the client cert/key live in Secrets Manager (never the sandbox).

variable "temporal_namespace" {
  type    = string
  default = "agent-sandbox.prod"
}

# Self-hosted alternative (only if not using Temporal Cloud):
# Aurora PostgreSQL (Multi-AZ, PITR) as the datastore; its RPO is the platform's RPO.
#
# resource "aws_rds_cluster" "temporal" {
#   engine                 = "aurora-postgresql"
#   database_name          = "temporal"
#   master_username        = "temporal"
#   manage_master_user_password = true
#   backup_retention_period = 7          # PITR; supports RPO <= 5 min target
#   # ... Multi-AZ instances, subnet group in private subnets, RDS Proxy for pooling ...
# }
