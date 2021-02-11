import ApexCharts from "apexcharts";
import { landingsData, Counter, getFirefoxReleases } from "./common.js";

let resultGraphs = document.getElementById("result-graphs");

async function renderComponentChangesChart(chartEl, bugSummaries) {
  // Only show fixed bugs.
  bugSummaries = bugSummaries.filter((bugSummary) => bugSummary.date !== null);

  if (bugSummaries.length == 0) {
    return;
  }

  let componentCounter = new Counter();
  for (let bugSummary of bugSummaries) {
    componentCounter[bugSummary.component] += 1;
  }

  let data = Object.entries(componentCounter)
    .filter(([component, count]) => count > 5)
    .map(([component, count]) => {
      return { x: component, y: count };
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
      text: "Component Changes",
    },
  };

  let chart = new ApexCharts(chartEl, options);
  chart.render();
}

async function renderSummary(bugSummaries) {
  resultGraphs.textContent = "";

  let componentChangesChartEl = document.createElement("div");
  resultGraphs.append(componentChangesChartEl);
  await renderComponentChangesChart(componentChangesChartEl, bugSummaries);
}

async function renderUI(rerenderSummary = true) {
  let data = await landingsData;

  let bugSummaries = [].concat.apply([], Object.values(data));

  let releases = await getFirefoxReleases();
  let latestRelease = Object.keys(releases).reduce((latestRelease, release) =>
    latestRelease > Number(release) ? latestRelease : Number(release)
  );

  bugSummaries = bugSummaries.filter((bugSummary) =>
    bugSummary.versions.includes(latestRelease)
  );

  if (rerenderSummary) {
    await renderSummary(bugSummaries);
  }
}

(async function init() {
  renderUI();
})();
