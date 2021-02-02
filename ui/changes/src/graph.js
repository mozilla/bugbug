import ApexCharts from "apexcharts";

// JSON from https://bugzilla.mozilla.org/show_bug.cgi?id=1669363
import teamComponentMapping from "./teams.json";
import { getComponentRegressionMap } from "./common";


async function generateData() {
  let map = await getComponentRegressionMap();
  return Object.keys(map[Object.keys(map)[0]])
    .map((element) => {
      let values = {};
      values.data = [];
      values.name = element;
      for (const value in map[element]) {
        let obj = {};
        obj.x = value;
        obj.y = Math.trunc(map[element][value] * 100);
        values.data.push(obj);
      }
      return values
    });
}

async function rerender(connections, teamComponentMapping) {
  var options = {
    series: [...(await generateData())],
    chart: {
      height: 700,
      width: 1200,
      type: "heatmap",
    },
    stroke: {
      width: 1,
    },
    plotOptions: {
      heatmap: {
        radius: 30,
        enableShades: true,
      },
    },
    dataLabels: {
      enabled: true,
      style: {
        colors: ["#fff"],
      },
    },
    colors: ["#008FFB"],
    xaxis: {
      type: "category",
    },
  };

  var chart = new ApexCharts(document.querySelector("#chart"), options);
  chart.render();

  let teamContainer = document.createElement("div");
  for (let team in teamComponentMapping) {
    let details = document.createElement("details");
    let summary = document.createElement("summary");
    summary.textContent = team;
    details.appendChild(summary);
    teamContainer.append(details);
  }
  document.querySelector("#team-view").appendChild(teamContainer);

  document.querySelector("#component-source").textContent = JSON.stringify(
    connections,
    null,
    2
  );
}

(async function () {
  let connectionsMap = await getComponentRegressionMap();
  console.log(connectionsMap);
  await rerender(connectionsMap, teamComponentMapping);
})();
