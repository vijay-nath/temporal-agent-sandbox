# EKS with a control/data-plane split across two node pools: a general pool for the API and a
# tainted, KVM-capable pool for Firecracker sandboxes, isolating untrusted execution.

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.project
  cluster_version = "1.31"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets

  eks_managed_node_groups = {
    general = {
      instance_types = ["m6i.large"]
      min_size       = 2
      max_size       = 6
      desired_size   = 2
      labels         = { workload = "general" }
    }

    sandbox = {
      # KVM-capable for Firecracker; tainted so only sandbox pods schedule here.
      instance_types = ["m6i.metal"]
      min_size       = 1
      max_size       = 10
      desired_size   = 1
      labels         = { workload = "sandbox" }
      taints = [{
        key    = "workload"
        value  = "sandbox"
        effect = "NO_SCHEDULE"
      }]
    }
  }
}

# Worker IAM (IRSA): read app secrets, write artifacts; nothing broader.
# Autoscaling (not shown): HPA on the API; KEDA on Temporal task-queue depth for the workers.
