// ETP tools for the autowebcompat-etp agent, inserted into the Firefox DevTools
// MCP bundle by patch-mcp.mjs at image build time.
//
// Each tool runs its chrome-privileged code the way the bundle's own
// get/set_firefox_prefs do: switch to the browser window's chrome context, run,
// then switch back to content in a `finally`. Unlike select_privileged_context,
// that never leaves the session stuck in chrome, so the page tools keep working.

// `isAsync` runs the script with executeAsyncScript: it must call its last
// argument with the result.
async function etpRunChrome(script, { isAsync = false } = {}, ...scriptArgs) {
  const { getFirefox: getFirefox2 } = await Promise.resolve().then(
    () => (init_src(), src_exports)
  );
  const firefox3 = await getFirefox2();
  const tree = await firefox3.sendBiDiCommand("browsingContext.getTree", {
    "moz:scope": "chrome",
  });
  const contexts = tree.contexts || [];
  if (contexts.length === 0) {
    throw new Error(
      "No privileged contexts available. Ensure MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1 is set."
    );
  }
  const driver = firefox3.getDriver();
  const originalContextId = firefox3.getCurrentContextId();
  try {
    await driver.switchTo().window(contexts[0].context);
    await driver.setContext("chrome");
    return isAsync
      ? await driver.executeAsyncScript(script, ...scriptArgs)
      : await driver.executeScript(script, ...scriptArgs);
  } finally {
    try {
      await driver.setContext("content");
      if (originalContextId) {
        await driver.switchTo().window(originalContextId);
      }
    } catch {}
  }
}

// Filter lists the content classifier can block with instead of the tracker
// lists. `probe` is a URL the list blocks and the other lists don't, so a match
// shows that this list in particular is loaded.
const ETP_FILTER_LISTS = {
  easyprivacy: {
    name: "EasyPrivacy",
    url: "https://easylist.to/easylist/easyprivacy.txt",
    probe: "https://www.google-analytics.com/analytics.js",
  },
  easylist: {
    name: "EasyList",
    url: "https://easylist.to/easylist/easylist.txt",
    probe: "https://ib.adnxs.com/ut/v3/prebid",
  },
};

// The modes that block with filter lists, with the lists each one uses.
const ETP_FILTER_LIST_MODES = {
  "strict/easyprivacy": [ETP_FILTER_LISTS.easyprivacy],
  "strict/easylist+easyprivacy": [
    ETP_FILTER_LISTS.easylist,
    ETP_FILTER_LISTS.easyprivacy,
  ],
};

// "off" is Standard with every classifier blocking feature turned off, so no
// tracker is blocked at all: Standard still blocks fingerprinters and
// cryptominers in normal windows.
const ETP_MODES = [
  "off",
  "standard",
  "strict",
  ...Object.keys(ETP_FILTER_LIST_MODES),
];

// The mode last set with set_etp_mode, which get_content_blocking_log records
// its logs under. Firefox starts in Standard.
let etpCurrentMode = "standard";

// Set by the harness in retest mode: keep the ETP exception list (both its
// baseline and convenience entries) off, so old bugs are tested against the
// bare lists. Skip-URL prefs still apply; they aren't part of either category.
const ETP_DISABLE_EXCEPTIONS = process.env.ETP_DISABLE_EXCEPTIONS === "1";

const ETP_SET_MODE_SCRIPT = `
  // listUrls is the test_list_urls value for a filter-list mode, else null.
  const [mode, listUrls, disableExceptions] = arguments;
  const category = "browser.contentblocking.category";
  const CC = "privacy.trackingprotection.content.";
  // The service ignores pref changes until it exists. Getting it creates it,
  // but fails while it is disabled, after creating it.
  try {
    Cc["@mozilla.org/content-classifier-service;1"].getService(
      Ci.nsIContentClassifierService
    );
  } catch {}
  // Clearing protection.enabled drops the classifier's engines, and setting
  // test_list_urls afterwards is always a change, which is what makes the
  // classifier download the list (enabling it alone does not).
  for (const p of [
    "protection.enabled",
    "protection.engines",
    "protection.engines.pbmode",
    "protection.test_list_urls",
  ]) {
    Services.prefs.clearUserPref(CC + p);
  }
  if (listUrls !== null) {
    // With the mirror on, switching the category below would overwrite the
    // engines prefs with the tracker-list engines.
    Services.prefs.setIntPref(CC + "mirror.mode", 0);
    Services.prefs.setStringPref(CC + "protection.engines", "test_block");
    Services.prefs.setStringPref(CC + "protection.engines.pbmode", "test_block");
    Services.prefs.setBoolPref(CC + "protection.enabled", true);
    Services.prefs.setStringPref(CC + "protection.test_list_urls", listUrls);
  }
  // ContentBlockingPrefs observes the category pref and synchronously applies
  // the category's prefs.
  Services.prefs.setStringPref(
    category,
    listUrls !== null ? "strict" : mode === "off" ? "standard" : mode
  );
  // Turning these off moves the category to "custom".
  if (mode === "off") {
    for (const p of [
      "privacy.trackingprotection.enabled",
      "privacy.trackingprotection.pbmode.enabled",
      "privacy.trackingprotection.socialtracking.enabled",
      "privacy.trackingprotection.cryptomining.enabled",
      "privacy.trackingprotection.fingerprinting.enabled",
      "privacy.trackingprotection.emailtracking.enabled",
      "privacy.trackingprotection.emailtracking.pbmode.enabled",
    ]) {
      Services.prefs.setBoolPref(p, false);
    }
  }
  // Switching the category resets these (Strict turns baseline on, Standard
  // clears both), so turn them off again after it. ContentBlockingPrefs
  // ignores them when matching the category, which stays as set above.
  if (disableExceptions) {
    for (const c of ["baseline", "convenience"]) {
      Services.prefs.setBoolPref(
        "privacy.trackingprotection.allow_list." + c + ".enabled",
        false
      );
    }
  }
  const { ContentBlockingPrefs } = ChromeUtils.importESModule(
    "moz-src:///browser/components/protections/ContentBlockingPrefs.sys.mjs"
  );
  const read = (branch, p) => {
    try {
      switch (branch.getPrefType(p)) {
        case branch.PREF_BOOL: return branch.getBoolPref(p);
        case branch.PREF_INT: return branch.getIntPref(p);
        case branch.PREF_STRING: return branch.getStringPref(p);
      }
    } catch {}
    return null;
  };
  const defaults = Services.prefs.getDefaultBranch("");
  const changed = {};
  for (const p of Object.keys(ContentBlockingPrefs.CATEGORY_PREFS.strict)) {
    const value = read(Services.prefs, p);
    const standard = read(defaults, p);
    if (value !== standard) {
      changed[p] = { value, standard };
    }
  }
  return { category: Services.prefs.getStringPref(category), changed };
`;

// Clears cookies, site data and caches, so the next load is a first visit like
// in a fresh Private Browsing window. Otherwise state from the previous mode
// carries over: e.g. a bot-protection cookie earned in Standard lets the site
// skip the challenge that breaks in Strict. Calls back with the flags of the
// cleaners that failed (0 if none).
const ETP_CLEAR_DATA_SCRIPT = `
  const done = arguments[arguments.length - 1];
  const S = Ci.nsIClearDataService;
  Services.clearData.deleteData(
    S.CLEAR_ALL_CACHES | S.CLEAR_COOKIES_AND_SITE_DATA,
    { onDataDeleted: (failedFlags) => done({ failedFlags }) }
  );
`;

// Starts the downloads Strict mode depends on, once per session, and reports
// their progress on every call: the tracker lists (Safe Browsing tables of the
// mozilla provider) and the url-classifier-exceptions Remote Settings
// collection. State lives on the chrome window, which persists between scripts.
//
// List updates are serialized, and each one is bracketed by
// safebrowsing-update-begin (data: provider) and safebrowsing-update-finished
// (data: "success", "update error: ..." or "download error: <status>"), so the
// result of a mozilla update is the "finished" that follows its "begin".
const ETP_LISTS_STATUS_SCRIPT = `
  const PROVIDER = "mozilla";
  const lastUpdate = () =>
    Services.prefs.getStringPref(
      "browser.safebrowsing.provider." + PROVIDER + ".lastupdatetime", ""
    );
  let state = window.__etpLists;
  if (!state) {
    state = window.__etpLists = { lists: {}, rs: { done: false } };
    if (lastUpdate() === "") {
      let provider = null;
      const observer = {
        observe(subject, topic, data) {
          if (topic === "safebrowsing-update-begin") {
            provider = data;
          } else if (provider === PROVIDER) {
            state.lists.result = data;
          }
        },
      };
      Services.obs.addObserver(observer, "safebrowsing-update-begin");
      Services.obs.addObserver(observer, "safebrowsing-update-finished");
      try {
        const started = Cc["@mozilla.org/url-classifier/listmanager;1"]
          .getService(Ci.nsIUrlListManager)
          .forceUpdates(
            Services.prefs.getStringPref(
              "browser.safebrowsing.provider." + PROVIDER + ".lists"
            )
          );
        if (!started) {
          state.lists.result = "update error: the list manager could not start an update";
        }
      } catch (e) {
        state.lists.result = "update error: " + e;
      }
    }
    const { RemoteSettings } = ChromeUtils.importESModule(
      "resource://services-settings/remote-settings.sys.mjs"
    );
    RemoteSettings.pollChanges({ trigger: "manual" })
      .then(() => RemoteSettings("url-classifier-exceptions").get())
      .then((records) => { state.rs = { done: true, exceptions: records.length }; })
      .catch((e) => { state.rs = { done: true, error: String(e) }; });
  }
  const result = state.lists.result ?? null;
  return {
    lists: {
      lastUpdate: lastUpdate(),
      result,
      failed: result !== null && result !== "success",
    },
    exceptionList: state.rs,
  };
`;

const ETP_LISTS_TIMEOUT_MS = 120e3;

// Waits until the tracker lists and the exception list are downloaded. Throws
// as soon as either download fails, or on timeout, with the status attached.
async function etpEnsureProtectionLists() {
  const deadline = Date.now() + ETP_LISTS_TIMEOUT_MS;
  for (;;) {
    const status = await etpRunChrome(ETP_LISTS_STATUS_SCRIPT);
    const details = JSON.stringify(status, null, 2);
    if (status.lists.failed) {
      throw new Error(`Updating the tracker lists failed:\n${details}`);
    }
    if (status.exceptionList.error) {
      throw new Error(`Syncing the ETP exception list failed:\n${details}`);
    }
    if (status.lists.lastUpdate !== "" && status.exceptionList.done) {
      return status;
    }
    if (Date.now() >= deadline) {
      throw new Error(
        `Timed out after ${
          ETP_LISTS_TIMEOUT_MS / 1e3
        }s waiting for the tracker ` +
          `lists and the ETP exception list:\n${details}`
      );
    }
    await new Promise((resolve2) => setTimeout(resolve2, 2e3));
  }
}

// Reports whether the content classifier's "test_block" engine blocks `url`.
// Until the downloads finish the engine doesn't exist (NS_ERROR_NOT_AVAILABLE);
// a list whose download fails is left out of it, so its probe never matches.
const ETP_PROBE_SCRIPT = `
  const done = arguments[arguments.length - 1];
  const url = arguments[0];
  const service = Cc["@mozilla.org/content-classifier-service;1"].getService(
    Ci.nsIContentClassifierService
  );
  const request = {
    url,
    sourceUrl: "https://example.com/",
    topWindowUrl: "https://example.com/",
    requestType: "script",
    privateBrowsing: false,
    forceThirdPartyToTop: false,
    isNonRecommendedAddon: false,
  };
  const notBuilt = (e) => e?.result === Cr.NS_ERROR_NOT_AVAILABLE;
  try {
    service
      .probeFeature("test_block", request)
      .then((r) => done({ ready: r.matched }))
      .catch((e) => done(notBuilt(e) ? { ready: false } : { error: String(e) }));
  } catch (e) {
    done(notBuilt(e) ? { ready: false } : { error: String(e) });
  }
`;

// Waits until the content classifier blocks with each of `lists`. Throws on
// timeout, which also covers a failed download.
async function etpWaitForFilterLists(lists) {
  const deadline = Date.now() + ETP_LISTS_TIMEOUT_MS;
  for (const list of lists) {
    for (;;) {
      const status = await etpRunChrome(
        ETP_PROBE_SCRIPT,
        { isAsync: true },
        list.probe
      );
      if (status.error) {
        throw new Error(
          `Probing the content classifier failed: ${status.error}`
        );
      }
      if (status.ready) {
        break;
      }
      if (Date.now() >= deadline) {
        throw new Error(
          `Timed out after ${ETP_LISTS_TIMEOUT_MS / 1e3}s waiting for the ` +
            `content classifier to load the ${list.name} list from ` +
            `${list.url} (it still doesn't block ${list.probe}); the ` +
            "download may have failed."
        );
      }
      await new Promise((resolve2) => setTimeout(resolve2, 1e3));
    }
  }
}

async function handleSetEtpMode(args2) {
  try {
    const { mode } = args2 || {};
    if (!ETP_MODES.includes(mode)) {
      throw new Error(
        `mode must be one of ${ETP_MODES.map((m) => `"${m}"`).join(", ")}`
      );
    }
    const filterLists = ETP_FILTER_LIST_MODES[mode] ?? null;
    // Without the lists, Strict mode blocks nothing: make sure they are there
    // before switching, and fail instead of handing back a Strict mode that
    // silently doesn't block.
    const lists = mode === "strict" ? await etpEnsureProtectionLists() : null;
    const result = await etpRunChrome(
      ETP_SET_MODE_SCRIPT,
      {},
      mode,
      filterLists && filterLists.map((list) => list.url).join("|"),
      ETP_DISABLE_EXCEPTIONS
    );
    // The mode's prefs are applied now, even if a later step fails.
    etpCurrentMode = mode;
    if (filterLists) {
      await etpWaitForFilterLists(filterLists);
    }
    const { failedFlags } = await etpRunChrome(ETP_CLEAR_DATA_SCRIPT, {
      isAsync: true,
    });
    if (failedFlags) {
      throw new Error(
        `Clearing cookies, site data and caches failed (flags ${failedFlags})`
      );
    }
    const changed = Object.entries(result.changed);
    return successResponse(
      `ETP category is now ${result.category}. ` +
        (changed.length
          ? "Prefs off their Standard values:\n" +
            changed
              .map(
                ([p, { value, standard }]) =>
                  `${p} = ${JSON.stringify(value)} (Standard: ${JSON.stringify(
                    standard
                  )})`
              )
              .join("\n") +
            "\n"
          : "All of its prefs are at their Standard values.\n") +
        (ETP_DISABLE_EXCEPTIONS
          ? "The ETP exception list is disabled (baseline and convenience), " +
            "as in every mode of this run.\n"
          : "") +
        (lists
          ? `Tracker lists updated ${new Date(
              Number(lists.lists.lastUpdate)
            ).toISOString()}; ${
              lists.exceptionList.exceptions
            } ETP exception records.\n`
          : "") +
        (filterLists
          ? `Blocking with ${filterLists
              .map((list) => `the ${list.name} list (${list.url})`)
              .join(" and ")} in the content classifier instead of the ` +
            "tracker lists; they are loaded and blocking. Blocked resources show as STATE_BLOCKED_TRACKING_CONTENT " +
            "(or STATE_REPLACED_TRACKING_CONTENT when shimmed).\n"
          : "") +
        "Cleared cookies, site data and caches, so the next load is a first visit.\n" +
        "Reload the page (navigate_page) for the change to apply to it."
    );
  } catch (error2) {
    return errorResponse(
      error2 instanceof Error ? error2 : new Error(String(error2))
    );
  }
}

// Decodes the selected tab's content blocking log; the flag names come from
// nsIWebProgressListener so they stay correct across Firefox versions.
const ETP_CONTENT_BLOCKING_LOG_SCRIPT = `
  const done = arguments[arguments.length - 1];
  const L = Ci.nsIWebProgressListener;
  const names = Object.keys(L).filter((k) =>
    /^STATE_.*(BLOCKED|LOADED|REPLACED|ALLOWED|COOKIES|PARTITIONED)/.test(k)
  );
  const browser = gBrowser.selectedBrowser;
  Promise.resolve(browser.getContentBlockingLog())
    .then((raw) => {
      const log = JSON.parse(raw || "{}");
      const origins = {};
      for (const [origin, entries] of Object.entries(log)) {
        origins[origin] = entries.map(([state, blocked, count]) => ({
          flags: names.filter((k) => state & L[k]),
          blocked,
          count,
        }));
      }
      done({ url: browser.currentURI.spec, origins });
    })
    .catch((e) => done({ error: String(e) }));
`;

// Flags of a tracker request the classifier blocked, or replaced with a shim
// (which also keeps the tracker from loading). Not mixed-content, Safe Browsing
// or cookie blocking.
const ETP_BLOCKED_TRACKER_FLAG_RE =
  /^STATE_(BLOCKED|REPLACED)_(TRACKING|SOCIALTRACKING|EMAILTRACKING|FINGERPRINTING|CRYPTOMINING)_CONTENT$/;

// The origins in `origins` (as decoded by ETP_CONTENT_BLOCKING_LOG_SCRIPT)
// with a blocked tracker.
function etpBlockedTrackers(origins) {
  return Object.keys(origins).filter((origin) =>
    origins[origin].some((entry) =>
      entry.flags.some((flag) => ETP_BLOCKED_TRACKER_FLAG_RE.test(flag))
    )
  );
}

// Appends a log to the JSON file named by ETP_CONTENT_BLOCKING_LOG_FILE, under
// the mode last set with set_etp_mode: { [mode]: [{ time, url, blockedTrackers, origins }, ...] }. The
// agent then publishes the file with the run, so the full logs don't have to go
// through its context. Does nothing if the variable isn't set.
async function etpRecordContentBlockingLog(mode, result, blocked) {
  const path = process.env.ETP_CONTENT_BLOCKING_LOG_FILE;
  if (!path) {
    return;
  }
  const { readFile, writeFile } = await import("node:fs/promises");
  let logs = {};
  try {
    logs = JSON.parse(await readFile(path, "utf8"));
  } catch (e) {
    if (e.code !== "ENOENT") {
      throw e;
    }
  }
  (logs[mode] ??= []).push({
    time: new Date().toISOString(),
    url: result.url,
    blockedTrackers: blocked,
    origins: result.origins,
  });
  await writeFile(path, JSON.stringify(logs, null, 2));
}

async function handleGetContentBlockingLog() {
  try {
    // Async, since getContentBlockingLog() may return a promise.
    const result = await etpRunChrome(ETP_CONTENT_BLOCKING_LOG_SCRIPT, {
      isAsync: true,
    });
    if (result.error) {
      throw new Error(result.error);
    }
    const blocked = etpBlockedTrackers(result.origins);
    await etpRecordContentBlockingLog(etpCurrentMode, result, blocked);
    return successResponse(
      `Content blocking log for ${result.url}:\n` +
        `Blocked trackers: ${blocked.length}` +
        (blocked.length ? ` (${blocked.join(", ")})` : "") +
        "\n" +
        JSON.stringify(result.origins, null, 2)
    );
  } catch (error2) {
    return errorResponse(
      error2 instanceof Error ? error2 : new Error(String(error2))
    );
  }
}

// navigate_page only waits for "interactive" (HTML parsed), before the
// scripts, iframes and widgets a page needs have loaded. Waiting inside
// evaluate_script instead fails: the bundle aborts every BiDi command after
// 10s. So poll document.readyState with short, separate commands until the
// load event, then give late scripts a moment. A page that never reaches
// "complete" is reported, not treated as an error: being stuck loading can be
// exactly the breakage under investigation.
const ETP_PAGE_STATE_EXPRESSION =
  "JSON.stringify({ readyState: document.readyState, url: location.href })";

async function handleWaitForPageLoad(args2) {
  try {
    const { timeoutSeconds = 30, settleSeconds = 2 } = args2 || {};
    const { getFirefox: getFirefox2 } = await Promise.resolve().then(
      () => (init_src(), src_exports)
    );
    const firefox3 = await getFirefox2();
    const started = Date.now();
    const deadline = started + timeoutSeconds * 1e3;
    let state = null;
    for (;;) {
      try {
        const result = await firefox3.sendBiDiCommand("script.evaluate", {
          expression: ETP_PAGE_STATE_EXPRESSION,
          target: { context: firefox3.getCurrentContextId() },
          awaitPromise: false,
        });
        if (result.type === "success") {
          state = JSON.parse(result.result.value);
        }
      } catch {
        // Mid-navigation the old document can go away under the command, and
        // a busy page can time it out; keep polling until the deadline.
      }
      if (
        (state && state.readyState === "complete") ||
        Date.now() >= deadline
      ) {
        break;
      }
      await new Promise((resolve2) => setTimeout(resolve2, 500));
    }
    const waited = ((Date.now() - started) / 1e3).toFixed(1);
    if (!state || state.readyState !== "complete") {
      return successResponse(
        `The page did not finish loading within ${timeoutSeconds}s: ` +
          `readyState is ${state ? `"${state.readyState}"` : "unknown"}` +
          (state ? ` at ${state.url}` : "") +
          ". If the report says the page is stuck loading, this is the breakage."
      );
    }
    await new Promise((resolve2) => setTimeout(resolve2, settleSeconds * 1e3));
    return successResponse(
      `The page finished loading after ${waited}s (${state.url}); waited ` +
        `${settleSeconds}s more for late scripts.`
    );
  } catch (error2) {
    return errorResponse(
      error2 instanceof Error ? error2 : new Error(String(error2))
    );
  }
}

// screenshot_page captures only the viewport (WebDriver's Take Screenshot).
// screenshot_full_page captures the whole page for the comparison agent,
// which diffs it between ETP modes, by stitching viewport screenshots:
//
// 1. Scroll down one viewport at a time and back to the top, so content that
//    only loads once scrolled into view (lazy images, ads, feeds) loads and
//    the page height settles before the capture.
// 2. Capture the top viewport as the user sees it.
// 3. Hide fixed elements and make sticky ones non-sticky (same layout), so
//    headers and banners don't repeat in every slice, then capture slices
//    from the top, each overlapping the previous one by `overlap` px. Each
//    slice is captured while it is in view, so pages that unload content out
//    of view (virtualized lists) still show it.
// 4. Stitch in a chrome canvas: check each slice's overlap against the
//    previous one and, if the page moved in between, place it at the offset
//    where the rows match. The top viewport from step 2 goes on top.
//
// Every step is its own short command, since the bundle aborts BiDi commands
// after 10s. Scrolling stops at the bottom or at maxHeight, and the image is
// cut off there too, so an endless feed doesn't scroll or grow forever.
const ETP_PAGE_METRICS = `JSON.stringify({
  y: window.scrollY,
  viewport: window.innerHeight,
  height: Math.max(
    document.documentElement.scrollHeight,
    document.body?.scrollHeight ?? 0
  ),
})`;

const etpScrollExpression = (statement) =>
  `(() => { ${statement}; return ${ETP_PAGE_METRICS}; })()`;

// Hides fixed elements and makes sticky ones non-sticky. Hides with opacity,
// not visibility, which a descendant can turn back on with
// `visibility: visible`. Incremental: it
// runs before every slice, since pages make elements fixed once scrolled
// (floating players, "back to top" buttons), and only touches elements it
// hasn't already. Covers open shadow roots, and fixed or sticky ::before and
// ::after, which it switches with an attribute and a stylesheet rule as
// pseudo-elements have no inline style. Runs in a sandbox, so the page's
// scripts can't see or undo it. Returns how many elements it has pinned.
const ETP_PIN_OVERLAYS_EXPRESSION = `(() => {
  const state = (window.__etpPinned ??= {
    styles: [],
    pseudos: [],
    seen: new WeakSet(),
    sheet: null,
  });
  const fix = { fixed: ["opacity", "0"], sticky: ["position", "relative"] };
  const visit = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) visit(el.shadowRoot);
      if (state.seen.has(el)) continue;
      const pinned = fix[getComputedStyle(el).position];
      if (pinned) {
        const [prop, value] = pinned;
        state.styles.push([el, prop, el.style.getPropertyValue(prop),
          el.style.getPropertyPriority(prop)]);
        el.style.setProperty(prop, value, "important");
        state.seen.add(el);
      }
      for (const pseudo of ["before", "after"]) {
        const position = getComputedStyle(el, "::" + pseudo).position;
        if (fix[position] && !el.hasAttribute("data-etp-pin-" + pseudo)) {
          el.setAttribute("data-etp-pin-" + pseudo, position);
          state.pseudos.push([el, pseudo]);
        }
      }
    }
  };
  visit(document.body ?? document.documentElement);
  if (state.pseudos.length && !state.sheet) {
    state.sheet = document.createElement("style");
    state.sheet.textContent = ["before", "after"].map((p) =>
      \`[data-etp-pin-\${p}="fixed"]::\${p} { opacity: 0 !important }
       [data-etp-pin-\${p}="sticky"]::\${p} { position: relative !important }\`
    ).join("\\n");
    (document.head ?? document.documentElement).append(state.sheet);
  }
  return state.styles.length + state.pseudos.length;
})()`;

const ETP_UNPIN_OVERLAYS_EXPRESSION = `(() => {
  const state = window.__etpPinned;
  if (!state) return;
  for (const [el, prop, value, priority] of state.styles) {
    if (value) el.style.setProperty(prop, value, priority);
    else el.style.removeProperty(prop);
  }
  for (const [el, pseudo] of state.pseudos) {
    el.removeAttribute("data-etp-pin-" + pseudo);
  }
  state.sheet?.remove();
  window.__etpPinned = undefined;
})()`;

async function etpEvaluate(
  firefox3,
  expression,
  sandbox,
  { context = firefox3.getCurrentContextId(), awaitPromise = false } = {}
) {
  const result = await firefox3.sendBiDiCommand("script.evaluate", {
    expression,
    target: { context, sandbox },
    awaitPromise,
  });
  if (result.type !== "success") {
    throw new Error(
      `script failed: ${JSON.stringify(result.exceptionDetails ?? result)}`
    );
  }
  return result.result.value;
}

const etpSleep = (ms) => new Promise((resolve2) => setTimeout(resolve2, ms));

// Freezes motion, so loads in different modes and neighbouring slices show
// the same frame (the approach of Playwright's `animations: "disabled"`).
// Finite CSS animations and transitions jump to their end, endless ones
// (spinners, marquees) back to their start, and videos pause on the frame
// 1s in (the first frame of very short clips). Runs in the page's realm: the
// animations and media elements are the page's own. Resolves within 3s, as a
// video that can't seek (not buffered there) must not hold up the capture.
const ETP_FREEZE_MOTION_EXPRESSION = `(async () => {
  let animations = 0;
  for (const animation of document.getAnimations()) {
    try {
      const end = animation.effect?.getComputedTiming().endTime;
      if (Number.isFinite(end)) animation.finish();
      else animation.cancel();
      animations++;
    } catch {}
  }
  const videos = [...document.querySelectorAll("video")];
  const frozen = videos.map((video) => new Promise((resolve) => {
    video.pause();
    const at = video.duration > 2 ? 1 : 0;
    if (video.readyState === 0 || video.currentTime === at) return resolve();
    video.addEventListener("seeked", resolve, { once: true });
    video.currentTime = at;
  }));
  await Promise.race([
    Promise.all(frozen),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
  return JSON.stringify({ animations, videos: videos.length });
})()`;

// Every browsing context in the selected tab: the page and its frames,
// including cross-origin iframes (e.g. video embeds).
function etpContexts(tree) {
  return tree.flatMap((context) => [
    context.context,
    ...etpContexts(context.children ?? []),
  ]);
}

// Freezes motion in the page and all its frames; returns the totals. A frame
// that is gone or can't run scripts is skipped.
async function etpFreezeMotion(firefox3) {
  const { contexts } = await firefox3.sendBiDiCommand(
    "browsingContext.getTree",
    { root: firefox3.getCurrentContextId() }
  );
  const total = { animations: 0, videos: 0 };
  for (const context of etpContexts(contexts)) {
    try {
      const counts = JSON.parse(
        await etpEvaluate(firefox3, ETP_FREEZE_MOTION_EXPRESSION, undefined, {
          context,
          awaitPromise: true,
        })
      );
      total.animations += counts.animations;
      total.videos += counts.videos;
    } catch {}
  }
  return total;
}

async function etpScroll(firefox3, statement) {
  return JSON.parse(
    await etpEvaluate(firefox3, etpScrollExpression(statement))
  );
}

async function etpCaptureViewport(firefox3) {
  const { data } = await firefox3.sendBiDiCommand(
    "browsingContext.captureScreenshot",
    { context: firefox3.getCurrentContextId(), origin: "viewport" }
  );
  return data;
}

// Scrolls to each slice position (`step` px apart) until the bottom or
// maxHeight, calling `atStop(page)` at each. Also stops when a step doesn't
// move the page (it can't scroll, e.g. it scrolls an inner container).
async function etpScrollSteps(firefox3, step, maxHeight, pauseMs, atStop) {
  let page = await etpScroll(firefox3, "window.scrollTo(0, 0)");
  await etpSleep(pauseMs);
  for (;;) {
    await atStop(page);
    const bottom = page.y + page.viewport;
    if (bottom >= page.height || bottom >= maxHeight) {
      return page;
    }
    const next = await etpScroll(
      firefox3,
      `window.scrollTo(0, ${page.y + step})`
    );
    await etpSleep(pauseMs);
    if (next.y <= page.y) {
      return page;
    }
    page = next;
  }
}

// Stitches the slices in a chrome canvas; see step 4 above. Calls back with
// the PNG and, per slice after the first, the shift applied and whether its
// overlap matched.
const ETP_STITCH_SCRIPT = `
  const done = arguments[arguments.length - 1];
  const [top, slices, maxHeight, overlap] = arguments;
  const XHTML = "http://www.w3.org/1999/xhtml";
  const load = async (data) => {
    const img = new Image();
    img.src = "data:image/png;base64," + data;
    await img.decode();
    const canvas = document.createElementNS(XHTML, "canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0);
    // One hash per pixel row, to compare overlaps by row.
    const { data: px } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const rowBytes = canvas.width * 4;
    const rows = new Int32Array(canvas.height);
    for (let r = 0; r < canvas.height; r++) {
      let h = 0x811c9dc5;
      for (let i = r * rowBytes, end = i + rowBytes; i < end; i++) {
        h = Math.imul(h ^ px[i], 0x01000193);
      }
      rows[r] = h;
    }
    return { canvas, rows };
  };
  // Share of the rows where \`cur\`, placed \`dy\` rows below the top of
  // \`prev\`, matches it; null if they overlap by fewer than 16 rows.
  const matchAt = (prev, cur, dy) => {
    const n = Math.min(prev.rows.length - dy, cur.rows.length);
    if (dy < 0 || n < 16) return null;
    let same = 0;
    for (let k = 0; k < n; k++) {
      if (prev.rows[dy + k] === cur.rows[k]) same++;
    }
    return same / n;
  };
  (async () => {
    const images = [];
    for (const slice of slices) {
      images.push(await load(slice.data));
    }
    const topImage = await load(top);
    // Device pixels per CSS pixel.
    const scale = images[0].canvas.height / slices[0].viewport;
    const maxShift = Math.round((overlap * scale) / 2);
    const placed = [0];
    const report = [];
    for (let i = 1; i < images.length; i++) {
      const expected = Math.round((slices[i].y - slices[i - 1].y) * scale);
      let best = { shift: 0, match: matchAt(images[i - 1], images[i], expected) ?? 1 };
      if (best.match < 1) {
        for (let d = 1; d <= maxShift && best.match < 1; d++) {
          for (const shift of [-d, d]) {
            const match = matchAt(images[i - 1], images[i], expected + shift);
            // Prefer no shift unless another is clearly better: animated
            // content in the overlap never matches anywhere.
            if (match !== null && match > best.match + 0.02) {
              best = { shift, match };
            }
          }
        }
      }
      // A partial match (e.g. a leftover overlay in every slice) says
      // nothing about where the slice belongs.
      if (best.shift !== 0 && best.match < 0.95) {
        best = { shift: 0, match: matchAt(images[i - 1], images[i], expected) ?? 1 };
      }
      placed.push(placed[i - 1] + expected + best.shift);
      report.push({ shift: best.shift, match: Math.round(best.match * 1000) / 1000 });
    }
    const last = images.length - 1;
    const canvas = document.createElementNS(XHTML, "canvas");
    canvas.width = images[0].canvas.width;
    canvas.height = Math.min(
      placed[last] + images[last].canvas.height,
      Math.round(maxHeight * scale)
    );
    const ctx = canvas.getContext("2d");
    images.forEach((image, i) => ctx.drawImage(image.canvas, 0, placed[i]));
    ctx.drawImage(topImage.canvas, 0, 0);
    const url = canvas.toDataURL("image/png");
    return { data: url.slice(url.indexOf(",") + 1), report };
  })().then(done, (e) => done({ error: String(e) }));
`;

async function handleScreenshotFullPage(args2) {
  try {
    const {
      saveTo,
      maxHeight = 15000,
      overlap = 200,
      scrollPauseMs = 300,
    } = args2 || {};
    if (!saveTo) {
      throw new Error("saveTo is required");
    }
    const { getFirefox: getFirefox2 } = await Promise.resolve().then(
      () => (init_src(), src_exports)
    );
    const firefox3 = await getFirefox2();

    // 1. Load what loads on scroll.
    const { viewport } = await etpScroll(firefox3, "");
    if (overlap < 16 || overlap > viewport / 2) {
      throw new Error(`overlap must be between 16 and ${viewport / 2}`);
    }
    await etpScrollSteps(
      firefox3,
      viewport,
      maxHeight,
      scrollPauseMs,
      () => {}
    );
    await etpScroll(firefox3, "window.scrollTo(0, 0)");
    // Let sticky headers and scroll-triggered animations settle at the top.
    await etpSleep(Math.max(scrollPauseMs, 500));

    // 2. The top as the user sees it, with motion frozen.
    const frozen = await etpFreezeMotion(firefox3);
    const top = await etpCaptureViewport(firefox3);

    // 3. The slices, without the overlays.
    const pin = () =>
      etpEvaluate(firefox3, ETP_PIN_OVERLAYS_EXPRESSION, "etp-stitch");
    let pinned = await pin();
    const slices = [];
    let page;
    try {
      page = await etpScrollSteps(
        firefox3,
        viewport - overlap,
        maxHeight,
        scrollPauseMs,
        async (at) => {
          // Scrolling can make more elements fixed, and start animations
          // and players again.
          pinned = await pin();
          const counts = await etpFreezeMotion(firefox3);
          frozen.animations = Math.max(frozen.animations, counts.animations);
          frozen.videos = Math.max(frozen.videos, counts.videos);
          slices.push({ ...at, data: await etpCaptureViewport(firefox3) });
        }
      );
    } finally {
      await etpEvaluate(firefox3, ETP_UNPIN_OVERLAYS_EXPRESSION, "etp-stitch");
      await etpScroll(firefox3, "window.scrollTo(0, 0)");
    }

    // 4. Stitch.
    const stitched = await etpRunChrome(
      ETP_STITCH_SCRIPT,
      { isAsync: true },
      top,
      slices,
      maxHeight,
      overlap
    );
    if (stitched.error) {
      throw new Error(`Stitching the screenshots failed: ${stitched.error}`);
    }
    const saved = await saveScreenshot(stitched.data, saveTo);
    const shifted = stitched.report.filter((r) => r.shift !== 0);
    const unmatched = stitched.report
      .map((r, i) => ({ ...r, slice: i + 1 }))
      .filter((r) => r.match < 0.9);
    saved.content[0].text +=
      ` Stitched from ${slices.length} viewport slices, with ${pinned} fixed or ` +
      "sticky elements left out after the top viewport, and motion frozen " +
      `(${frozen.animations} animations, ${frozen.videos} videos).` +
      (page.height > maxHeight
        ? ` Captured only the top ${maxHeight}px of the ${page.height}px page.`
        : "") +
      (shifted.length
        ? ` ${shifted.length} slice(s) were realigned because the page moved ` +
          "while scrolling."
        : "") +
      (unmatched.length
        ? " The overlap of slice(s) " +
          unmatched
            .map((r) => `${r.slice} (${Math.round(r.match * 100)}%)`)
            .join(", ") +
          " didn't match the previous slice (animated content, or the page " +
          "changed while scrolling), so the image may be off there."
        : "");
    return saved;
  } catch (error2) {
    return errorResponse(
      error2 instanceof Error ? error2 : new Error(String(error2))
    );
  }
}

var screenshotFullPageTool = {
  name: "screenshot_full_page",
  description:
    "Capture the whole page of the selected tab, not just the viewport, as a PNG saved to saveTo. Scrolls through the page first so lazy-loaded content loads, then stitches overlapping viewport screenshots, with fixed and sticky elements shown only at the top. Freezes motion for the capture: CSS animations jump to their end (endless ones to their start) and videos pause 1s in, so the same page gives the same image; the animations are not restored afterwards. Pages taller than maxHeight are captured only down to maxHeight.",
  inputSchema: {
    type: "object",
    properties: {
      saveTo: {
        type: "string",
        description: "File path to save the screenshot to",
      },
      maxHeight: {
        type: "number",
        description:
          "How far down to scroll and capture, in CSS pixels (default 15000)",
      },
      overlap: {
        type: "number",
        description:
          "How much each slice overlaps the previous one, in CSS pixels (default 200)",
      },
      scrollPauseMs: {
        type: "number",
        description: "Pause after each scroll step (default 300)",
      },
    },
    required: ["saveTo"],
  },
};

var waitForPageLoadTool = {
  name: "wait_for_page_load",
  description:
    'Wait until the selected page has fully loaded (document.readyState is "complete"), then a little longer for late scripts. Call it after navigate_page and after clicks that load content, instead of waiting inside evaluate_script. Reports a page that never finishes loading instead of failing.',
  inputSchema: {
    type: "object",
    properties: {
      timeoutSeconds: {
        type: "number",
        description: "How long to wait for the load (default 30)",
      },
      settleSeconds: {
        type: "number",
        description: "Extra wait after the load for late scripts (default 2)",
      },
    },
  },
};

var setEtpModeTool = {
  name: "set_etp_mode",
  description:
    'Switch Enhanced Tracking Protection to the Standard or Strict category at runtime (no restart). "off" is Standard with every tracker-blocking classifier feature turned off. Firefox applies all of the category\'s prefs at once; the response lists the ones off their Standard values. Also clears all cookies, site data and caches, so the next load is a first visit (logins and site state are lost). Switching to Strict fails with an error if the tracker lists or the ETP exception list can\'t be downloaded. "strict/easyprivacy" and "strict/easylist+easyprivacy" are Strict, but block with the EasyPrivacy list, or EasyList and EasyPrivacy together (via the content classifier), instead of the tracker lists, fingerprinter and cryptominer lists and the ETP exception list; they wait until the lists are downloaded and blocking, and fail if they aren\'t. Reload the page afterwards. Requires MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1.',
  inputSchema: {
    type: "object",
    properties: {
      mode: {
        type: "string",
        enum: ETP_MODES,
        description:
          "ETP category to switch to; strict/easyprivacy is Strict with the EasyPrivacy list as the only block list, strict/easylist+easyprivacy with EasyList and EasyPrivacy as the only block lists",
      },
    },
    required: ["mode"],
  },
};

var getContentBlockingLogTool = {
  name: "get_content_blocking_log",
  description:
    "Return the content blocking log of the selected tab: every third-party origin with its STATE_* flags (e.g. STATE_BLOCKED_SOCIALTRACKING_CONTENT names the feature that blocked it), headed by the number of blocked trackers: origins with a STATE_BLOCKED_* or STATE_REPLACED_* tracker flag. Does not change the selected page. Requires MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1.",
  inputSchema: { type: "object", properties: {} },
};
