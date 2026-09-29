# Live PVC Copy and Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a warm PVC copy that reads the active source through an explicit read-only HostPath mount, then finalize and rebind through a separate safe `switch` command.

**Architecture:** The warm-copy command runs a source helper pinned to the active workload node and reads only the existing kubelet-mounted PV path; a destination helper mounts the target PVC and exposes a temporary ClusterIP rsync daemon. The switch command first requires the source claim to have no active pod consumers, then uses separate PVC-mounting helpers for the final cross-node rsync and calls the existing Retain-safe claim rebind flow.

**Tech Stack:** Python 3.13, Kubernetes Python client, `instrumentisto/rsync-ssh`, rsync daemon protocol, existing Clypi CLI.

**Spec:** `docs/superpowers/specs/2026-09-29-live-copy-switch-design.md`

## Global Constraints

- The warm-copy source is a read-only HostPath to the existing PVC mount; the live PVC must not be mounted again by CSI.
- The warm copy is non-authoritative and omits `--delete`; only the final switch sync deletes destination-only files.
- The switch refuses to proceed while any pod object, including a completed pod, references the source PVC.
- Both backing PVs must have reclaim policy `Retain` before claim rebind; neither PV is deleted.
- Source and destination run in separate pods and communicate only through a temporary in-cluster ClusterIP Service.
- The unrelated GitOps deletion was resolved by fast-forwarding the local GitOps checkout before migration; current render plan has no deletions.
- The HostPath source reader runs as UID/GID 65532; the destination rsync daemon retains the existing root identity needed to preserve numeric ownership. Both disable privilege escalation and use `RuntimeDefault` seccomp.
- Add no dependencies and do not alter existing user changes in the `kopy` worktree.
- Do not add or run tests; verify CLI help, generated manifests, and the actual staged/live migration path.

## Review Focus

- More than one active pod mounts the source PVC: the warm-copy command must fail before creating helpers.
- The source pod restarts or is replaced during warm copy: the command must fail rather than report a completed warm sync.
- The source kubelet PV mount directory is absent: HostPath type checking must fail closed without creating a path on the host.
- The destination PVC remains Pending or its helper cannot become Ready: cleanup must remove the temporary Service and pods, preserving both claims.
- A pod still consumes the source PVC or either PV is not Retain during switch: switch must fail before copying or deleting any PVC.

---

### Task 1: Add Kubernetes lookup and helper resource builders

**Files:**
- Modify: `kopy/k8s.py`

**Interfaces:**
- Produce `KubeClient.get_pvc_consumers(namespace: str, pvc_name: str) -> list[dict[str, Any]]`, returning non-terminal pods that reference the claim.
- Produce `KubeClient.get_pvc_mount_source(namespace: str, pvc_name: str) -> dict[str, Any]`, returning the unique ready consumer's pod name, UID, node name, and source PV name; reject zero or multiple consumers.
- Produce `build_hostpath_source_pod_manifest(pod_name: str, image: str, host_path: str, mount_path: str, node_name: str, rsync_command: list[str]) -> dict[str, Any]`.
- Produce target rsync Service manifest builder with a selector unique to the operation ID and TCP port 1873.
- Add create/delete/wait methods for the temporary Service and a helper to resolve the source PV mount path as `/var/lib/kubelet/pods/{pod_uid}/volumes/kubernetes.io~csi/{pv_name}/mount`.

- [x] **Step 1: Implement consumer discovery and source PV resolution**

  Filter terminal (`Succeeded`/`Failed`) and deleting pods; inspect pod PVC volumes. Resolve `.spec.volumeName` from the claim and verify the PV is CSI-backed before building the mount path.

- [x] **Step 2: Implement the read-only HostPath source manifest**

  Pin the pod with `spec.nodeName`, mount only the exact PV directory read-only, set `hostPath.type: Directory`, run UID/GID 65532, set non-root, no privilege escalation, and RuntimeDefault seccomp. The command runs the supplied rsync client command and exits with its status.

- [x] **Step 3: Implement operation-scoped target Service lifecycle**

  Select only the target helper for this invocation. Add API methods to create the Service, wait for a routable ClusterIP, and delete it idempotently. Preserve the destination rsync daemon's existing root identity for numeric ownership and add no privilege escalation plus RuntimeDefault seccomp.

---

### Task 2: Implement the non-authoritative HostPath warm copy

**Files:**
- Modify: `kopy/models.py`
- Modify: `kopy/workflow.py`
- Modify: `kopy/transport.py`

**Interfaces:**
- Add `source_host_mount: bool` to `CopyRequest`.
- Produce `run_hostpath_copy(request: CopyRequest, kube: object, helper_image: str, mount_path: str, rsync_port: int, rsync_bin: str) -> CopySession`.
- `run_copy` routes PVC-to-PVC requests with `source_host_mount=True` to this workflow.

- [x] **Step 1: Create the destination helper and Service**

  Mount the target PVC using the existing target rsync daemon manifest; expose its port through the operation-scoped ClusterIP Service and wait until the pod and Service are ready.

- [x] **Step 2: Run the source-side warm sync**

  Discover the one active source pod, create a read-only HostPath helper pinned to its node, run `rsync -a --numeric-ids /source/ rsync://SERVICE:1873/volume/` without `--delete`, and compare the source pod UID after completion.

- [x] **Step 3: Guarantee cleanup and fail-closed behavior**

  Delete the source pod, target pod, and Service in `finally`; surface failed waits or rsync status and never report a successful warm copy if the source pod changed.

---

### Task 3: Implement final sync and dedicated PVC switch

**Files:**
- Modify: `kopy/models.py`
- Modify: `kopy/workflow.py`
- Modify: `kopy/k8s.py`

**Interfaces:**
- Produce `SwitchRequest(source: Endpoint, target: Endpoint, context_name: str | None, namespace: str | None)`, where `source` is the canonical PVC and `target` is the staged PVC.
- Produce `run_switch(request: SwitchRequest, kube: object, helper_image: str, mount_path: str, rsync_port: int, rsync_bin: str) -> MoveSession`.

- [x] **Step 1: Validate switch preconditions**

  Require root PVC endpoints, bound claims, no pod references of any phase on the source PVC, and `Retain` on both source and destination PVs before creating copy helpers.

- [x] **Step 2: Copy the final delta across nodes**

  Start a source helper mounting the source PVC normally and a target helper mounting the staged PVC. Expose the target daemon through a temporary ClusterIP Service; run `rsync -a --delete --numeric-ids` from source to destination.

- [x] **Step 3: Rebind only after the final sync succeeds**

  Call the existing `run_takeover` flow with staged PVC as its source and canonical PVC as its target. On any copy error, clean temporary resources and leave both claims and PVs untouched.

---

### Task 4: Expose the explicit CLI and document the migration sequence

**Files:**
- Modify: `kopy/cli.py`
- Modify: `README.md`

**Interfaces:**
- Add `--source-host-mount` to `kopy copy`; its help text must say it reads the existing source volume through a read-only HostPath and avoids a second CSI mount.
- Add `kopy switch SOURCE_PVC TARGET_PVC --namespace NAMESPACE` with canonical PVC first and staged PVC second.

- [x] **Step 1: Add the copy flag and switch command**

  Pass the flag through `build_copy_request`; construct a `SwitchRequest` for the new command and call `run_switch`.

- [x] **Step 2: Update CLI and README instructions**

  Document warm copy while the app runs, external workload stop, `kopy switch`, and the fact that the source PV remains Retain/Released for operator cleanup.

- [x] **Step 3: Verify without running tests**

  Run `uv run kopy copy --help` and `uv run kopy switch --help`; inspect the generated source and target pod manifests for HostPath read-only, target PVC, node placement, and RuntimeDefault seccomp. Do not modify or run the existing test suite.

---

### Task 5: Execute the VictoriaMetrics migration with the new CLI

**Files:**
- Use: `/Users/jan.werner/projects/homelab/kluctl/platform/50-monitoring/20-workload/helm-values.yaml`
- Use: the Homelab repository's `homelabctl` GitOps render/publish flow

- [x] **Step 1: Recheck cluster state and source Retain policy**

  Confirm VM is running on node 2, source PV policy is `Retain`, destination PVC uses `openebs-lvm-retain`, and both PVC names and PV IDs match the approved migration.

- [x] **Step 2: Perform the warm copy**

  Run `uv run kopy copy pvc://vmsingle-victoriametrics pvc://vmsingle-migration-staging --namespace monitoring --source-host-mount` and verify successful completion; VM remains running.

- [x] **Step 3: Stop VMSingle through GitOps**

  Publish only `replicaCount: 0` while keeping `storageClassName: hcloud-volumes`; apply with the source GitOps flow and wait until no pod references the source PVC. Do not change the StorageClass while the old PVC is bound.

- [x] **Step 4: Pause the PVC-owning operator**

  Set `vmsingle.spec.paused: true` through GitOps and wait until the CR reflects it. The VictoriaMetrics operator owns the canonical PVC and otherwise recreates it from its current HCloud spec during rebind.

- [x] **Step 5: Perform final switch and restart**

  Final `rsync --delete` completed through the new `switch` workflow. Rebind first waited on a completed 11-day-old Pod referencing the PVC; after deleting that stale Pod, `kopy rebind-pvc pvc://vmsingle-migration-staging pvc://vmsingle-victoriametrics --namespace monitoring` completed the interrupted claim swap. Then GitOps changed storage to `openebs-lvm-retain`, restored one replica, and removed the pause. Verified the canonical PVC is bound to LVM, VMSingle is Running on node 1, the `up` query returns 42 series, and the Hetzner PV remains Retain/Released.
