# Production Infrastructure

This directory contains an illustrative Terraform configuration showing how the local implementation can evolve into a production deployment.

The local development environment uses Docker Compose for simplicity, while the production design targets Kubernetes with isolated sandbox execution. The application architecture, workflow boundaries, and sandbox interface remain consistent across both environments.

## Local vs. Production

| Local Development                   | Production                                   |
| ----------------------------------- | -------------------------------------------- |
| gVisor (`runsc`)                    | Firecracker microVMs via Kata `RuntimeClass` |
| Docker Compose                      | Kubernetes (EKS)                             |
| Local Temporal server               | Temporal Cloud (recommended)                 |
| Environment variables               | Secrets Manager / Kubernetes Secrets         |
| Published API port                  | Application Load Balancer                    |
| Docker socket (trusted worker only) | Kubernetes API creating one-shot Pods        |

## Infrastructure Responsibilities

Terraform provisions the foundational infrastructure, including:

- VPC and networking
- EKS cluster and node groups
- IAM roles and IRSA
- Secrets management
- Object storage
- Supporting cloud resources

Application deployment, RuntimeClasses, NetworkPolicies, autoscaling, and other in-cluster resources are expected to be managed separately using a GitOps workflow.

## Production Considerations

A production deployment would typically include:

- Firecracker-based sandbox isolation
- OIDC authentication
- Secret management
- Network segmentation
- Centralized monitoring and alerting
- Autoscaling
- Disaster recovery planning
- Tenant-aware rate limiting

The Terraform in this directory is intentionally illustrative and demonstrates how the application architecture maps cleanly from local development to a production platform without changing the application code or workflow model.
