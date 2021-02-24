import { Temporal } from "proposal-temporal/lib/index.mjs";
import * as common from "./common.js";

let resultGraphs = document.getElementById("result-graphs");

async function renderUI() {
  resultGraphs.textContent = "";

  const bugSummaries = await common.getFilteredBugSummaries();

  let riskChartEl = document.createElement("div");
  resultGraphs.append(riskChartEl);
  await common.renderRiskChart(riskChartEl, bugSummaries);

  let regressionsChartEl = document.createElement("div");
  resultGraphs.append(regressionsChartEl);
  await common.renderRegressionsChart(regressionsChartEl, bugSummaries);

  let typesChartEl = document.createElement("div");
  resultGraphs.append(typesChartEl);
  await common.renderTypesChart(typesChartEl, bugSummaries);

  let fixTimesChartEl = document.createElement("div");
  resultGraphs.append(fixTimesChartEl);
  await common.renderFixTimesChart(fixTimesChartEl, bugSummaries);

  let patchCoverageChartEl = document.createElement("div");
  resultGraphs.append(patchCoverageChartEl);
  await common.renderPatchCoverageChart(patchCoverageChartEl, bugSummaries);

  let reviewTimeChartEl = document.createElement("div");
  resultGraphs.append(reviewTimeChartEl);
  await common.renderReviewTimeChartElChart(reviewTimeChartEl, bugSummaries);
}

(async function init() {
  let startDate = Temporal.now.plainDateISO().subtract({ years: 1 }).toString();
  document.getElementById("createStartDate").value = document.getElementById(
    "fixStartDate"
  ).value = startDate;

  await common.setupOptions(renderUI);

  await renderUI();
})();
