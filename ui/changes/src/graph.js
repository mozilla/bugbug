import ApexCharts from "apexcharts";

// JSON from https://bugzilla.mozilla.org/show_bug.cgi?id=1669363
import teamComponentMapping from "./teams.json";
import { getComponentRegressionMap } from "./common";

function generateData(count, yrange) {
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

function rerender(connections, teamComponentMapping) {
  var options = {
    series: [
      {
        name: "Metric1",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric2",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric3",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric4",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric5",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric6",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric7",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric8",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
      {
        name: "Metric8",
        data: generateData(20, {
          min: 0,
          max: 90,
        }),
      },
    ],
    chart: {
      height: 300,
      width: 800,
      type: "heatmap",
    },
    stroke: {
      width: 0,
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
  rerender(connectionsMap, teamComponentMapping);
})();
