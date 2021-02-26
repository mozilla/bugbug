import ApexCharts from "apexcharts";

import { getComponentRegressionMap } from "./common";

async function generateData() {
  let map = await getComponentRegressionMap();
  return Object.keys(map[Object.keys(map)[0]]).map((element) => {
    let values = {};
    values.data = [];
    values.name = element;
    for (const value in map[element]) {
      let obj = {};
      obj.x = value;
      obj.y = Math.trunc(map[element][value] * 100);
      values.data.push(obj);
    }
    return values;
  });
}

async function rerender(connections) {
  var options = {
    series: [...(await generateData())],
    chart: {
      height: 700,
      width: 1200,
      type: "heatmap",
      animations: {
        enabled: false,
      },
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

  document.querySelector("#component-source").textContent = JSON.stringify(
    connections,
    null,
    2
  );
}

(async function () {
  let connectionsMap = await getComponentRegressionMap();
  console.log(connectionsMap);
  await rerender(connectionsMap);
})();
