## Building and verifying a change

1. Build Firefox with the `build_firefox` tool.
   The build is your schema check — if it fails, read the error; it usually names the offending field.
   If the intervention uses inline `css`, reusable scripts, `hide_alerts`,
   `hide_messages` or `modify_meta_viewport`, add the generated file names
   (e.g. `bug<id>-<label>.js`) to `preprocessed_intervention_files.mozbuild`;
   a wrong list fails the build with the exact names. The build only
   notices added or removed intervention files. If you edit an existing intervention,
   run `touch {interventions_dir}` before rebuilding. After a rebuild,
   call `restart_firefox` before using the DevTools tools again. If you're unable to build it, report
   `build_failed` rather than packaging the add-on by hand.

2. Re-run the reproduction script against your build:
   {run_script}
   It must exit with 0. If it still exits with 1, iterate or report
   `no_intervention_possible`. Never report a fix you did not observe.

3. If the script exits 0, inspect the reported site with the DevTools tools.
   The script only proves the reported symptom is gone, so also check that:

   - the result matches the expected layout or behavior described in the
     report or its analysis (for example the sizes and positions of the
     affected elements), not just that the broken state is gone;
   - nothing else visibly breaks on the reported page: load it, scroll
     through it, and try the page elements the intervention touches;
   - if the platforms include `android`, also check a phone-sized viewport with
     `set_viewport_size`.

   Stay on the reported page. Only take idempotent actions.
   Do not submit forms, log in, create accounts, make purchases or bookings,
   send messages, or start calls.
