import * as common from "./common.js";

async function renderUI() {
  const data = await common.landingsData;
  const bugID = Number(common.getOption("bugID"));

  // 1433500

  const allBugSummaries = [].concat.apply([], Object.values(data));

  for (const bugSummary of allBugSummaries) {
    if (bugSummary["id"] == bugID) {
      await common.renderTable([bugSummary]);
      return;
    }
  }

  await common.renderTable([]);
}

(async function init() {
  await common.setupOptions(renderUI);

  await renderUI();
})();
