# The production sandbox boundary as Kubernetes objects (normally managed by GitOps). The
# RuntimeClass, restricted PodSecurity, and default-deny NetworkPolicy mirror the local gVisor
# flags; a pod securityContext and the Kyverno guard below complete the mapping.

# Firecracker via Kata: the worker submits a one-shot Pod with this RuntimeClass, scheduled
# onto the tainted KVM "sandbox" node pool.
resource "kubernetes_manifest" "runtimeclass_firecracker" {
  manifest = {
    apiVersion = "node.k8s.io/v1"
    kind       = "RuntimeClass"
    metadata   = { name = "firecracker" }
    handler    = "kata-fc"
    scheduling = {
      nodeSelector = { workload = "sandbox" }
      tolerations  = [{ key = "workload", value = "sandbox", effect = "NoSchedule" }]
    }
  }
}

# Sandbox namespace under restricted Pod Security Standards.
resource "kubernetes_manifest" "ns_sandbox" {
  manifest = {
    apiVersion = "v1"
    kind       = "Namespace"
    metadata = {
      name = "sandbox"
      labels = {
        "pod-security.kubernetes.io/enforce" = "restricted"
      }
    }
  }
}

# Default-deny network policy, mirroring the local --network none (Firecracker pods have no NIC).
resource "kubernetes_manifest" "netpol_default_deny" {
  manifest = {
    apiVersion = "networking.k8s.io/v1"
    kind       = "NetworkPolicy"
    metadata   = { name = "default-deny", namespace = "sandbox" }
    spec = {
      podSelector = {}
      policyTypes = ["Ingress", "Egress"]
    }
  }
}

# Kyverno admission policies would enforce runtimeClassName=firecracker, no hostNetwork, no
# added capabilities, and readOnlyRootFilesystem on every sandbox pod.
