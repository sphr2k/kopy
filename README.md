# kopy

Copy data between local paths and Kubernetes PVC targets.

## Examples

Create a missing target PVC during a PVC-to-PVC copy and use the cluster default `StorageClass`:

```bash
kopy pvc://vmsingle-victoriametrics pvc://vmsingle-victoriametrics-migrated --create-pvc
```

Create the target PVC with an explicit `StorageClass`:

```bash
kopy pvc://vmsingle-victoriametrics pvc://vmsingle-victoriametrics-migrated --create-pvc --storage-class fast-ssd
```

Copy a PVC to a migrated PVC and rebind the original name only if the copy succeeds:

```bash
kopy move pvc://vmsingle-victoriametrics pvc://vmsingle-victoriametrics-migrated --storage-class fast-ssd
```

Rebind the original PVC name after migration:

```bash
kopy rebind-pvc pvc://vmsingle-victoriametrics-migrated pvc://vmsingle-victoriametrics
```

For an online warm copy, explicitly read the source through its existing read-only HostPath mount. This avoids a second CSI mount of the live PVC:

```bash
kopy copy pvc://vmsingle-victoriametrics pvc://vmsingle-migration-staging --namespace monitoring --source-host-mount
```

After stopping the workload, run the final sync and rebind as a separate step:

```bash
kopy switch pvc://vmsingle-victoriametrics pvc://vmsingle-migration-staging --namespace monitoring
```

Temporarily switch non-`Retain` PVs to `Retain` during rebind and restore their original reclaim policy afterwards:

```bash
kopy rebind-pvc pvc://vmsingle-victoriametrics-migrated pvc://vmsingle-victoriametrics --set-retain
```

## Notes

- `--create-pvc` only creates a missing target PVC when the source is also a PVC, because `kopy` copies size and access modes from the source claim.
- `move` only works on PVC root endpoints such as `pvc://name` and automatically creates the migrated target PVC when needed.
- `--source-host-mount` is for a warm PVC-to-PVC copy while its workload runs. It requires exactly one Ready source pod and reads the existing kubelet volume mount through a read-only HostPath.
- `switch` requires the source workload to be stopped, its PVC-owning controller paused so it cannot recreate the old claim, all pods referencing the source claim deleted (including completed pods), and both backing PVs to use `Retain`. It final-syncs with deletion enabled, rebinds the staged PV to the original claim name, and leaves the old source PV `Retain`/`Released` for separate cleanup.
- Helper pods tolerate all taints by default.
- `rebind-pvc` only works on PVC root endpoints such as `pvc://name`.
- `rebind-pvc` requires both backing PVs to use reclaim policy `Retain` and aborts otherwise, unless `--set-retain` is passed.
- `--set-retain` patches only the PVs that need it and restores their original reclaim policy after the rebind completes.
