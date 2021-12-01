import * as common from "./common.js";

var all_filters_ids = ["releaseVersions_release_html"];
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

let resultGraphs = document.getElementById("result-graphs");

async function renderComponentChangesChart(chartEl, bugSummaries) {
  // Only show fixed bugs.
  bugSummaries = bugSummaries.filter((bugSummary) => bugSummary.date !== null);

  if (bugSummaries.length == 0) {
    return;
  }

  let dimension = common.getOption("changeGrouping")[0];

  let componentCounter = new common.Counter();
  for (let bugSummary of bugSummaries) {
    componentCounter[bugSummary[dimension]] += 1;
  }

  common.renderTreemap(
    chartEl,
    `${dimension.charAt(0).toUpperCase()}${dimension.slice(1)} changes`,
    componentCounter
  );
}

async function renderAffectedComponentChangesChart(chartEl, bugSummaries) {
  // Only consider fixed bugs.
  bugSummaries = bugSummaries.filter((bugSummary) => bugSummary.date !== null);

  if (bugSummaries.length == 0) {
    return;
  }

  let componentCounter = new common.Counter();
  for (let bugSummary of bugSummaries) {
    componentCounter[bugSummary["component"]] += 1;
  }

  let componentConnectionMap = await common.getComponentDependencyMap(
    "regressions"
  );

  let affectedComponentCounter = new common.Counter();
  for (let [sourceComponent, count] of Object.entries(componentCounter)) {
    if (!componentConnectionMap.hasOwnProperty(sourceComponent)) {
      continue;
    }

    for (let [targetComponent, percentage] of Object.entries(
      componentConnectionMap[sourceComponent]
    )) {
      affectedComponentCounter[targetComponent] += count * percentage;
    }
  }

  common.renderTreemap(
    chartEl,
    "Most affected components",
    affectedComponentCounter
  );
}

async function renderUI() {
  resultGraphs.textContent = "";

  const bugSummaries = await common.getFilteredBugSummaries();

  let componentChangesChartEl = document.createElement("div");
  resultGraphs.append(componentChangesChartEl);
  await renderComponentChangesChart(componentChangesChartEl, bugSummaries);

  let affectedComponentChangesChartEl = document.createElement("div");
  resultGraphs.append(affectedComponentChangesChartEl);
  await renderAffectedComponentChangesChart(
    affectedComponentChangesChartEl,
    bugSummaries
  );
}

(async function init() {
  await common.setupOptions(renderUI);

  await renderUI();
})();
