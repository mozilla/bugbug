# Windows installer

`browser/installer/windows/nsis/`. This is **NSIS**: `installer.nsi` (the full installer), `stub.nsi` (the small downloader stub), `uninstaller.nsi`, `maintenanceservice_installer.nsi`, and the `.nsh` include files that hold most of the logic. Localized strings live in the `.nsi`/`.properties` files alongside. The packaging manifests are `browser/installer/package-manifest.in` and `browser/installer/allowed-dupes.mn`, and the MSI and MSIX wrappers are in the sibling `msi/` and `msix/` directories. There is no JS here at all. Note which installer the bug is about: the stub and the full installer are separate programs with separate code.

## Tests

coverage is thin and specific. `browser/installer/windows/nsis/test/xpcshell/test_stub_installer.js` drives `test_stub.nsi` and covers the **stub** installer only; nothing exercises `installer.nsi` or the uninstaller. So for most Installer bugs an empty `relevant_tests` is the correct answer — say that the area is uncovered rather than leaving the reader to wonder whether you looked.
