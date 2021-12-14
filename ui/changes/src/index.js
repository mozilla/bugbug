import * as common from "./common.js";

let resultSummary = document.getElementById("result-summary");
let resultGraphs = document.getElementById("result-graphs");

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
  let testingChartEl = document.createElement("div");
  resultGraphs.append(testingChartEl);
  common.renderTestingChart(testingChartEl, bugSummaries);

  let riskChartEl = document.createElement("div");
  resultGraphs.append(riskChartEl);
  await common.renderRiskChart(riskChartEl, bugSummaries);

  let regressionsChartEl = document.createElement("div");
  resultGraphs.append(regressionsChartEl);
  await common.renderRegressionsChart(regressionsChartEl, bugSummaries);

  let severityChartEl = document.createElement("div");
  resultGraphs.append(severityChartEl);
  await common.renderSeverityChart(severityChartEl, bugSummaries);

  let fixTimesChartEl = document.createElement("div");
  resultGraphs.append(fixTimesChartEl);
  await common.renderFixTimesChart(fixTimesChartEl, bugSummaries);

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
