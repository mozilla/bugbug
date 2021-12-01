import { Temporal } from "@js-temporal/polyfill";
import * as common from "./common.js";

var all_filters_ids = [
  "teams_team_html",
  "components_team_html",
  //"types_team_html",
];
var k = {};
for (var i = 0; i < all_filters_ids.length; i++) {
  k[all_filters_ids[i]] = 0;
}
var button_prefix = ["", "Undo "];

function selectAll(el_id) {
  // get select element
  var el;

  var s_index = el_id.search("_");
  if (s_index != -1) {
    el = document.getElementById(String(el_id.slice(0, s_index)));
  } else {
    el = document.getElementById(el_id);
  }

  for (var p = 0; p < el.options.length; p++) {
    if (!k[el_id]) {
      el.options[p].selected = true;
    } else {
      el.options[p].selected = false;
    }
  }
  k[el_id] = !k[el_id];

  var select_all_el = document.getElementById("select_all_" + el_id);
  select_all_el.textContent = button_prefix[Number(k[el_id])] + "Select All";
}

document.getElementById("select_all_" + all_filters_ids[0]).onclick =
  function () {
    selectAll(all_filters_ids[0]);
  };
document.getElementById("select_all_" + all_filters_ids[1]).onclick =
  function () {
    selectAll(all_filters_ids[1]);
  };

/*
document.getElementById("select_all_" + all_filters_ids[2]).onclick =
  function () {
    selectAll(all_filters_ids[2]);
  };

*/

let resultGraphs = document.getElementById("result-graphs");
const dependencySection = document.getElementById("dependency-section");

async function renderUI() {
  resultGraphs.textContent = "";
  dependencySection.textContent = "";

  const bugSummaries = await common.getFilteredBugSummaries();

  let riskChartEl = document.createElement("div");
  resultGraphs.append(riskChartEl);
  await common.renderRiskChart(riskChartEl, bugSummaries);

  const riskListEl = await common.renderRiskList(bugSummaries);
  resultGraphs.append(riskListEl);
  resultGraphs.append(document.createElement("br"));

  let regressionsChartEl = document.createElement("div");
  resultGraphs.append(regressionsChartEl);
  await common.renderRegressionsChart(regressionsChartEl, bugSummaries, true);

  let severityChartEl = document.createElement("div");
  resultGraphs.append(severityChartEl);
  await common.renderSeverityChart(severityChartEl, bugSummaries, true);

  let fixTimesChartEl = document.createElement("div");
  resultGraphs.append(fixTimesChartEl);
  await common.renderFixTimesChart(fixTimesChartEl, bugSummaries);

  const fixTimesListEl = await common.renderFixTimesList(bugSummaries);
  resultGraphs.append(fixTimesListEl);
  resultGraphs.append(document.createElement("br"));

  let patchCoverageChartEl = document.createElement("div");
  resultGraphs.append(patchCoverageChartEl);
  await common.renderPatchCoverageChart(patchCoverageChartEl, bugSummaries);

  const patchCoverageListEl = await common.renderPatchCoverageList(
    bugSummaries
  );
  resultGraphs.append(patchCoverageListEl);
  resultGraphs.append(document.createElement("br"));

  let reviewTimeChartEl = document.createElement("div");
  resultGraphs.append(reviewTimeChartEl);
  await common.renderReviewTimeChart(reviewTimeChartEl, bugSummaries);

  const reviewTimeListEl = await common.renderReviewTimeList(bugSummaries);
  resultGraphs.append(reviewTimeListEl);
  resultGraphs.append(document.createElement("br"));

  let assignTimeChartEl = document.createElement("div");
  resultGraphs.append(assignTimeChartEl);
  await common.renderTimeToAssignChart(assignTimeChartEl, bugSummaries);

  let testFailureStatsChartEl = document.createElement("div");
  resultGraphs.append(testFailureStatsChartEl);
  await common.renderTestFailureStatsChart(testFailureStatsChartEl);

  const testFailureListEl = await common.renderTestFailureList();
  resultGraphs.append(testFailureListEl);
  resultGraphs.append(document.createElement("br"));

  let testSkipStatsChartEl = document.createElement("div");
  resultGraphs.append(testSkipStatsChartEl);
  await common.renderTestSkipStatsChart(testSkipStatsChartEl);

  const external_components = common.allComponents.filter(
    (component) => !common.getOption("components").includes(component)
  );

  const dependencyHeatmapChartEl = document.createElement("div");
  dependencySection.append(dependencyHeatmapChartEl);
  await common.renderDependencyHeatmap(
    dependencyHeatmapChartEl,
    "Dependencies from external components (columns) to selected components (rows)",
    external_components,
    common.getOption("components")
  );
}

(async function init() {
  let startDate = Temporal.Now.plainDateISO().subtract({ years: 1 }).toString();
  document.getElementById("createStartDate").value = document.getElementById(
    "fixStartDate"
  ).value = startDate;

  await common.setupOptions(renderUI);

  await renderUI();
})();
