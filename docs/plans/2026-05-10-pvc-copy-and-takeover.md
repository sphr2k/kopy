# PVC Copy And Takeover Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend `kopy copy` so it can optionally create a missing target PVC, and add a separate `takeover-pvc` command for safe PVC name takeover workflows.

**Architecture:** Keep data movement inside the existing copy workflow and add a small PVC orchestration layer around it. Implement takeover as a separate guarded workflow that validates reclaim policy and rebind prerequisites before replacing the original claim name with the migrated volume.

**Tech Stack:** Python, `clypi`, Kubernetes Python client, `pytest`

---

### Task 1: Model and CLI surface

**Files:**
- Modify: `kopy/models.py`
- Modify: `kopy/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**
- Add tests for `build_copy_request(..., create_pvc=True, storage_class=...)`.
- Add tests for `build_takeover_request(...)`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`

**Step 3: Write minimal implementation**
- Add request fields for copy PVC creation.
- Add a takeover request model and request builder.
- Wire a new `takeover-pvc` entrypoint in `kopy.cli`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`

### Task 2: PVC manifest helpers and taint tolerance

**Files:**
- Modify: `kopy/k8s.py`
- Test: `tests/test_workflow.py`
- Test: `tests/test_debug_manifest.py`

**Step 1: Write the failing test**
- Add tests for helper pod tolerations.
- Add tests for PVC manifest creation with and without explicit `storageClassName`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow.py tests/test_debug_manifest.py -v`

**Step 3: Write minimal implementation**
- Add shared helper pod tolerations.
- Add a helper to build PVC manifests from source PVC shape plus optional storage class override.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow.py tests/test_debug_manifest.py -v`

### Task 3: Copy workflow PVC creation

**Files:**
- Modify: `kopy/workflow.py`
- Modify: `kopy/k8s.py`
- Test: `tests/test_copy_run.py`

**Step 1: Write the failing test**
- Add a copy workflow test that creates the target PVC when missing and `--create-pvc` is set.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_copy_run.py -v`

**Step 3: Write minimal implementation**
- Detect missing target PVC in PVC-to-PVC copy.
- Create the PVC from source characteristics and optional storage class override.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_copy_run.py -v`

### Task 4: Takeover workflow

**Files:**
- Modify: `kopy/workflow.py`
- Modify: `kopy/k8s.py`
- Modify: `kopy/cli.py`
- Test: `tests/test_copy_run.py`

**Step 1: Write the failing test**
- Add a workflow test that validates retain-only takeover and rebind behavior.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_copy_run.py -v`

**Step 3: Write minimal implementation**
- Add guarded takeover workflow.
- Require root PVC endpoints, bound PVs, and `Retain` reclaim policy.
- Rebind the target PV to a recreated PVC with the source name.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_copy_run.py -v`

### Task 5: Full verification

**Files:**
- Modify: `README.md`
- Test: `tests/test_cli.py`
- Test: `tests/test_workflow.py`
- Test: `tests/test_debug_manifest.py`
- Test: `tests/test_copy_run.py`

**Step 1: Run focused tests**

Run: `pytest tests/test_cli.py tests/test_workflow.py tests/test_debug_manifest.py tests/test_copy_run.py -v`

**Step 2: Update docs**
- Document `--create-pvc`, optional `--storage-class`, and `takeover-pvc`.

**Step 3: Run full test suite**

Run: `pytest -v`
