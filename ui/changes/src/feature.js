import * as common from "./common.js";

let resultSummary = document.getElementById("result-summary");
let resultGraphs = document.getElementById("result-graphs");

async function renderFeatureChangesChart(chartEl, bugSummaries) {
  // Only show fixed bugs.
  bugSummaries = bugSummaries.filter((bugSummary) => bugSummary.date !== null);

  if (bugSummaries.length == 0) {
    return;
  }

  let metabugs = (await common.featureMetabugs).reduce((acc, val) => {
    acc[val.id] = val.summary;
    return acc;
  }, {});

  let featureCounter = new common.Counter();
  for (let bugSummary of bugSummaries) {
    for (let bugID of bugSummary["meta_ids"]) {
      featureCounter[metabugs[bugID]] += 1;
    }
  }

  const metabug_summary_to_id = Object.entries(metabugs).reduce(
    (acc, [id, summary]) => {
      acc[summary] = id;
      return acc;
    },
    {}
  );

  common.renderTreemap(chartEl, `Feature metabug changes`, featureCounter, 0, {
    dataPointSelection: function (event, chartContext, config) {
      const summary = Object.keys(featureCounter)[config.dataPointIndex];

      const metaBugID = document.getElementById("metaBugID");
      metaBugID.value = metabug_summary_to_id[summary];
      const syntheticEvent = new Event("change");
      metaBugID.dispatchEvent(syntheticEvent);
    },
  });
}

async function renderSummary(bugSummaries) {
  let metaBugID = common.getOption("metaBugID");

  let changesets = [];
  if (bugSummaries.length) {
    changesets = bugSummaries
      .map((summary) => summary.commits.length)
      .reduce((a, b) => a + b);
  }

  let bugText = metaBugID ? `For bug ${metaBugID}: ` : ``;
  let summaryText = `${bugText}There are ${bugSummaries.length} bugs with ${changesets} changesets.`;
  resultSummary.textContent = summaryText;

  resultGraphs.textContent = "";

  let featureChangesChartEl = document.createElement("div");
  resultGraphs.append(featureChangesChartEl);
  await renderFeatureChangesChart(featureChangesChartEl, bugSummaries);

  let riskChartEl = document.createElement("div");
  resultGraphs.append(riskChartEl);
  await common.renderRiskChart(riskChartEl, bugSummaries);

  let regressionsChartEl = document.createElement("div");
  resultGraphs.append(regressionsChartEl);
  await common.renderRegressionsChart(regressionsChartEl, bugSummaries);

  let timeToBugChartEl = document.createElement("div");
  resultGraphs.append(timeToBugChartEl);
  await common.renderTimeToBugChart(timeToBugChartEl, bugSummaries);

  let timeToConfirmChartEl = document.createElement("div");
  resultGraphs.append(timeToConfirmChartEl);
  await common.renderTimeToConfirmChart(timeToConfirmChartEl, bugSummaries);
}

async function renderUI(rerenderSummary = true) {
  const bugSummaries = await common.getFilteredBugSummaries();

  if (rerenderSummary) {
    await renderSummary(bugSummaries);
  }

  await common.renderTable(bugSummaries);
}

(async function init() {
  await common.setupOptions(renderUI);

  await renderUI();
})();
