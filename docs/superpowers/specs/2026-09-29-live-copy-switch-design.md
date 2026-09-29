# Live PVC Copy and Switch Design

## Goal

Let `kopy` warm-copy a PVC while its workload continues running, then minimize
downtime with an explicit final sync and PVC rebind. This is intended for
same-cluster migrations between storage classes and nodes.

## Verified storage behavior

Hetzner CSI supports a single-node multi-writer capability, but a second pod
mounting this ext4 PVC on the same node currently fails because the driver
attempts a second filesystem mount and receives `Resource busy`. A read-only
HostPath mount of the existing kubelet pod-volume directory succeeds. The
source PV is location-bound to `fsn1`; the destination LVM class is constrained
to node 1, so a final transfer must use separate source and destination pods.

## CLI workflow

1. `kopy copy pvc://SOURCE pvc://TARGET --live` performs a warm, non-authoritative
   sync. It requires the source PVC to be mounted by exactly one active pod.
2. The operator stops the workload through its normal control plane and waits
   until no active pod references the source PVC.
3. `kopy switch pvc://SOURCE pvc://TARGET` performs an authoritative final
   `rsync --delete` from a source pod mounting the source PVC to a destination
   pod mounting the target PVC, then rebinds the target PV to the original PVC
   name. It requires both PVs to use `Retain` and does not delete either PV.

## Copy architecture

For the warm copy, `kopy` discovers the active source pod, source PV, node, and
pod UID. It starts a read-only, non-root helper on that same node, mounting only
the existing kubelet directory for the source PV as HostPath. A separate
destination helper mounts the target PVC. The helpers transfer data over a
temporary in-cluster ClusterIP service using the configured rsync helper image.
The source helper is removed after the transfer, and the source pod UID is
checked again so a workload restart cannot silently turn the result into a
claimed-complete copy.

For `switch`, the source helper mounts the PVC normally after the workload is
stopped. PV topology schedules it on an eligible cloud node, while the target
helper mounts the LVM PVC on node 1. The transfer uses the same temporary
ClusterIP transport. Once rsync succeeds, the existing retain-safe rebind flow
releases the staging PVC and binds its PV under the source claim name.

## Safety and failure behavior

- The warm copy is explicitly non-authoritative; only `switch` performs the
  final deletion sync and claim rebind.
- `--live` fails if it cannot identify one ready source pod and the source PV
  mount directory. It never falls back to mounting the live source PVC again.
- `switch` fails before copying if an active pod still references the source
  PVC, or if either PV is not `Retain`.
- The source HostPath mount is read-only and points to one PV mount directory;
  helper containers run non-root with privilege escalation disabled.
- Temporary pods and Service are deleted on success and failure. A failed
  final sync leaves both PVCs and PVs available for retry.
- NetworkPolicy and image-pull failures are reported as transfer errors; no
  cluster-wide policy or CSI configuration is changed.

## Scope

This adds a live-source mode for PVC-to-PVC copies and a dedicated `switch`
command. Existing local-to-PVC, PVC-to-local, ordinary PVC-to-PVC, and `move`
behavior remain available. Snapshot support and application-specific backup
formats are out of scope.
