# Application updater

`toolkit/mozapps/update/`. `.sys.mjs` modules (`AppUpdater.sys.mjs`, `UpdateService.sys.mjs`, `BackgroundUpdate.sys.mjs`), the XPCOM interfaces in `nsIUpdateService.idl`, and the C++ updater binary under `toolkit/mozapps/update/updater/`. Update behavior is heavily driven by prefs under `app.update.*` and by the state written to the update directory, so read `common/` for the shared constants and status codes.

## Tests

`toolkit/mozapps/update/tests/` — xpcshell under `unit_aus_update/`, `unit_background_update/`, and `unit_update_binary/`, browser-chrome under `browser/`, plus `marionette/` and C++ `gtest/`.
