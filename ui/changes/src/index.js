import * as common from "./common.js";

// for Select All button

var all_filters_ids = [
  "releaseVersions",
  "types",
  "testingTags",
  "teams",
  "components",
  "severities",
  "riskiness",
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
document.getElementById("select_all_" + all_filters_ids[2]).onclick =
  function () {
    selectAll(all_filters_ids[2]);
  };
document.getElementById("select_all_" + all_filters_ids[3]).onclick =
  function () {
    selectAll(all_filters_ids[3]);
  };
document.getElementById("select_all_" + all_filters_ids[4]).onclick =
  function () {
    selectAll(all_filters_ids[4]);
  };
document.getElementById("select_all_" + all_filters_ids[5]).onclick =
  function () {
    selectAll(all_filters_ids[5]);
  };
document.getElementById("select_all_" + all_filters_ids[6]).onclick =
  function () {
    selectAll(all_filters_ids[6]);
  };

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
