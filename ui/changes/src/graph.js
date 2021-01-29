import ApexCharts from "apexcharts";

// JSON from https://bugzilla.mozilla.org/show_bug.cgi?id=1669363
import teamComponentMapping from "./teams.json";
import { getComponentRegressionMap } from "./common";

function _generateData(count, yrange) {
  var i = 0;
  var series = [];
  while (i < count) {
    var x = (i + 1).toString();
    var y =
      Math.floor(Math.random() * (yrange.max - yrange.min + 1)) + yrange.min;

    series.push({
      x: x,
      y: y,
    });
    i++;
  }
  return series;
}

async function generateData() {
  let map = await getComponentRegressionMap();
  let return_map = [];
  Object.keys(map)
    .slice(0, 10)
    .forEach(element => {
      let values = {};
      values.data = [];
      values.name = element;
      for (const value in map[element]){
        let obj = {};
        obj.x = value;
        obj.y = Math.trunc(map[element][value] * 100);
        values.data.push(obj);
      }
      return_map.push(values);
    });
  return return_map;
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
        enableShades: false,
        colorScale: {
          ranges: [
            {
              from: 0,
              to: 50,
              color: "#008FFB",
            },
            {
              from: 51,
              to: 100,
              color: "#00E396",
            },
          ],
        },
      },
    },
    dataLabels: {
      enabled: true,
      style: {
        colors: ["#fff"],
      },
    },
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
