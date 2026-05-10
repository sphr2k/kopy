# kopy

Copy a local directory into a Kubernetes target.

## Examples

Create a missing target PVC during a PVC-to-PVC copy and use the cluster default `StorageClass`:

```bash
kopy pvc://vmsingle-victoriametrics pvc://vmsingle-victoriametrics-migrated --create-pvc
```

Create the target PVC with an explicit `StorageClass`:

```bash
kopy pvc://vmsingle-victoriametrics pvc://vmsingle-victoriametrics-migrated --create-pvc --storage-class fast-ssd
```

Take over the original PVC name after migration:

```bash
kopy takeover-pvc pvc://vmsingle-victoriametrics-migrated pvc://vmsingle-victoriametrics
```

Temporarily switch non-`Retain` PVs to `Retain` during takeover and restore their original reclaim policy afterwards:

```bash
kopy takeover-pvc pvc://vmsingle-victoriametrics-migrated pvc://vmsingle-victoriametrics --set-retain
```

## Notes

- `--create-pvc` only creates a missing target PVC when the source is also a PVC, because `kopy` copies size and access modes from the source claim.
- Helper pods tolerate all taints by default.
- `takeover-pvc` only works on PVC root endpoints such as `pvc://name`.
- `takeover-pvc` requires both backing PVs to use reclaim policy `Retain` and aborts otherwise, unless `--set-retain` is passed.
- `--set-retain` patches only the PVs that need it and restores their original reclaim policy after the takeover completes.
