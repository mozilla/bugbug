import ApexCharts from "apexcharts";
import * as common from "./common.js";

let resultGraphs = document.getElementById("result-graphs");

async function renderTreemap(chartEl, title, counter) {
  let data = Object.entries(counter)
    .filter(([name, count]) => count > 5)
    .map(([name, count]) => {
      return { x: name, y: count };
    });

  let options = {
    series: [
      {
        data: data,
      },
    ],
    legend: {
      show: false,
    },
    chart: {
      type: "treemap",
    },
    title: {
      text: title,
    },
  };

  let chart = new ApexCharts(chartEl, options);
  chart.render();
}

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

  renderTreemap(
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

  let componentConnectionMap = await common.getComponentRegressionMap();

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

  renderTreemap(chartEl, "Most affected components", affectedComponentCounter);
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

  renderUI();
})();
