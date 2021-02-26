import ApexCharts from "apexcharts";

import { TESTING_TAGS, getTestingPolicySummaryData } from "./common.js";

(function () {
  let charts = {
    all: null,
    backedout: null,
    regression: null,
  };
  let radios = [...document.querySelectorAll("input[name=grouping]")];
  var previouslySelected = document.querySelector(
    "input[name=grouping]:checked"
  );
  for (let radio of radios) {
    radio.addEventListener("change", function () {
      if (this !== previouslySelected) {
        previouslySelected = this;
        rerender(this.value);
      }
    });
  }

  function rerenderChart(name, data, grouping) {
    let series = [];
    let colors = [];
    for (let tag in TESTING_TAGS) {
      series.push({ name: TESTING_TAGS[tag].label, data: [] });
      colors.push(TESTING_TAGS[tag].color);
    }

    let dataContainer = document.querySelector(`#${name}-data`);
    dataContainer.textContent = "\t";

    dataContainer.textContent += "total commits\t";
    for (let tag in TESTING_TAGS) {
      dataContainer.textContent += `${tag}\t`;
    }

    for (let date in data) {
      dataContainer.textContent += `\n`;
      dataContainer.textContent += `${date}\t`;

      let total = 0;
      for (let tag in data[date]) {
        total += data[date][tag];
      }
      dataContainer.textContent += `${total}\t`;
      for (let tag in data[date]) {
        // dataContainer.textContent += `${Math.round(parseFloat((data[date][tag] / total) * 100))}% (${data[date][tag]})\t`;
        // dataContainer.textContent += `${Math.round(parseFloat((data[date][tag] / total) * 100))}%\t`;
        dataContainer.textContent += `${data[date][tag]}\t`;
      }
    }

    let xaxisCategories = [];
    for (let date in data) {
      xaxisCategories.push(date);
      let i = 0;
      for (let tag in data[date]) {
        series[i].data.push(data[date][tag]);
        i++;
      }
    }

    let annotations =
      grouping === "monthly"
        ? undefined
        : {
            xaxis: [
              {
                x: "2020-09-20",
                borderColor: "#775DD0aa",
                offsetX: 0,
                label: {
                  style: {
                    color: "orange",
                  },
                  text: "rollout",
                },
              },
            ],
          };

    var options = {
      series,
      colors,
      chart: {
        type: "bar",
        height: 300,
        stacked: true,
        toolbar: {
          show: true,
        },
        zoom: {
          enabled: true,
        },
        animations: {
          enabled: false,
        },
      },
      plotOptions: {
        bar: {
          horizontal: false,
        },
      },
      xaxis: {
        categories: xaxisCategories,
      },
      annotations,
    };

    // TODO: Print summary percentages etc on top of graph
    let chartContainer = document.querySelector(`#${name}-chart`);
    if (charts[name]) {
      charts[name].destroy();
    }

    charts[name] = new ApexCharts(
      chartContainer.querySelector(".chart"),
      options
    );
    charts[name].render();
  }

  async function rerender(grouping) {
    let allData = await getTestingPolicySummaryData(grouping);
    rerenderChart("all", allData, grouping);

    let backedoutData = await getTestingPolicySummaryData(grouping, (bug) => {
      return bug.commits.some((c) => {
        return c.backedout;
      });
    });
    rerenderChart("backedout", backedoutData, grouping);

    let regressionData = await getTestingPolicySummaryData(grouping, (bug) => {
      return bug.regressor;
    });
    rerenderChart("regression", regressionData, grouping);
  }
  rerender(previouslySelected ? previouslySelected.value : undefined);
})();
