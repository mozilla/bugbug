// TODO: On click, show previous components affected by similar patches.
// TODO: On click, show previous bugs caused by similar patches.

import { Temporal } from 'proposal-temporal/lib/index.mjs';

import ApexCharts from 'apexcharts'

import {
  TESTING_TAGS,
  featureMetabugs,
  landingsData,
  getNewTestingTagCountObject,
} from "./common.js";

const HIGH_RISK_COLOR = "rgb(255, 13, 87)";
const MEDIUM_RISK_COLOR = "darkkhaki";
const LOW_RISK_COLOR = "green";

let options = {
  metaBugID: {
    value: null,
    type: "text",
  },
  testingTags: {
    value: null,
    type: "select",
  },
  startDate: {
    value: null,
    type: "text",
  },
  endDate: {
    value: null,
    type: "text",
  },
  riskinessEnabled: {
    value: null,
    type: "checkbox",
  },
};

if (new URLSearchParams(window.location.search).has("riskiness")) {
  document.querySelector("#riskinessEnabled").checked = true;
}
let resultSummary = document.getElementById("result-summary");
let metabugsDropdown = document.querySelector("#featureMetabugs");

// TODO: port this to an option maybe
async function buildMetabugsDropdown() {
  metabugsDropdown.addEventListener("change", () => {
    setOption("metaBugID", metabugsDropdown.value);
    rebuildTable();
  });
  let bugs = await featureMetabugs;
  metabugsDropdown.innerHTML = `<option value="" selected>Choose a feature metabug</option>`;
  for (let bug of bugs) {
    let option = document.createElement("option");
    option.setAttribute("value", bug.id);
    option.textContent = bug.summary;
    metabugsDropdown.append(option);
  }
}

function getOption(name) {
  return options[name].value;
}

function getOptionType(name) {
  return options[name].type;
}

function setOption(name, value) {
  return (options[name].value = value);
}

function addRow(bugSummary) {
  let table = document.getElementById("table");

  let row = table.insertRow(table.rows.length);

  let num_column = document.createElement("td");
  num_column.append(document.createTextNode(table.rows.length - 1));
  row.append(num_column);

  let bug_column = row.insertCell(1);
  let bug_link = document.createElement("a");
  bug_link.textContent = `Bug ${bugSummary["id"]}`;
  bug_link.href = `https://bugzilla.mozilla.org/show_bug.cgi?id=${bugSummary["id"]}`;
  bug_link.target = "_blank";
  bug_column.append(bug_link);
  bug_column.append(document.createTextNode(` - ${bugSummary["summary"]}`));

  let components_percentages = Object.entries(
    bugSummary["most_common_regression_components"]
  );
  if (components_percentages.length > 0) {
    let component_container = document.createElement("div");
    component_container.classList.add("desc-box");
    bug_column.append(component_container);
    components_percentages.sort(
      ([component1, percentage1], [component2, percentage2]) =>
        percentage2 - percentage1
    );
    component_container.append(
      document.createTextNode("Most common regression components:")
    );
    let component_list = document.createElement("ul");
    for (let [component, percentage] of components_percentages.slice(0, 3)) {
      let component_list_item = document.createElement("li");
      component_list_item.append(
        document.createTextNode(
          `${component} - ${Math.round(100 * percentage)}%`
        )
      );
      component_list.append(component_list_item);
    }
    component_container.append(component_list);
  }

  /*<hr>
          The patches have a high chance of causing regressions of type <b>crash</b> and <b>high severity</b>.
          <br><br>
          The patches could affect the <b>Search</b> and <b>Bookmarks</b> features.
          <br><br>
          Examples of previous bugs caused by similar patches:
          <ul>
            <li>Bug 1 - Can"t bookmark pages</li>
            <li>Bug 7 - Search doesn"t work anymore <span style="background-color:gold;color:yellow;">STR</span></li>
          </ul>*/

  let date_column = row.insertCell(2);
  date_column.textContent = bugSummary.date;

  let testing_tags_column = row.insertCell(3);
  testing_tags_column.classList.add("testing-tags");
  let testing_tags_list = document.createElement("ul");
  for (let commit of bugSummary.commits) {
    let testing_tags_list_item = document.createElement("li");
    if (!commit.testing) {
      testing_tags_list_item.append(document.createTextNode("unknown"));
    } else {
      testing_tags_list_item.append(
        document.createTextNode(TESTING_TAGS[commit.testing].label)
      );
    }
    testing_tags_list.append(testing_tags_list_item);
  }
  testing_tags_column.append(testing_tags_list);

  let coverage_column = row.insertCell(4);
  let lines_added = 0;
  let lines_covered = 0;
  let lines_unknown = 0;
  for (let commit of bugSummary.commits) {
    if (commit["coverage"]) {
      lines_added += commit["coverage"][0];
      lines_covered += commit["coverage"][1];
      lines_unknown += commit["coverage"][2];
    }
  }
  if (lines_added != 0) {
    if (lines_unknown != 0) {
      coverage_column.textContent = `${lines_covered}-${lines_covered + lines_unknown} of ${lines_added}`;
    } else {
      coverage_column.textContent = `${lines_covered} of ${lines_added}`;
    }
  } else {
    coverage_column.textContent = "";
  }

  if (getOption("riskinessEnabled")) {
    let risk_list = document.createElement("ul");
    let risk_column = row.insertCell(5);

    let risk_text = document.createElement("span");
    risk_text.textContent = `${bugSummary.risk_band} risk`;
    if (bugSummary.risk_band == "l") {
        // Lower than average risk.
        risk_text.style.color = LOW_RISK_COLOR;
        risk_text.textContent = 'Lower risk';
    } else if (bugSummary.risk_band == "a") {
        // Average risk.
        risk_text.style.color = MEDIUM_RISK_COLOR;
        risk_text.textContent = 'Average risk';
    } else {
        // Higher than average risk.
        risk_text.style.color = HIGH_RISK_COLOR;
        risk_text.textContent = 'Higher risk';
    }

    risk_column.append(risk_text);
  }
}

function renderTestingSummary(bugSummaries) {
  let metaBugID = getOption("metaBugID");

  let changesets = [];
  if (bugSummaries.length) {
    changesets = bugSummaries
      .map((summary) => summary.commits.length)
      .reduce((a, b) => a + b);
  }

  let testingCounts = getNewTestingTagCountObject();
  bugSummaries.forEach((summary) => {
    summary.commits.forEach((commit) => {
      if (!commit.testing) {
        testingCounts.unknown++;
      } else {
        testingCounts[commit.testing] = testingCounts[commit.testing] + 1;
      }
    });
  });

  let bugText = metaBugID ? `For bug ${metaBugID}: ` : ``;
  let summaryText = `${bugText}There are ${bugSummaries.length} bugs with ${changesets} changesets.`;
  resultSummary.textContent = summaryText;

  // let pre = document.createElement("pre");
  // pre.textContent = `${JSON.stringify(
  //   testingCounts
  // )}`;
  // resultSummary.append(pre);

  let categories = [];
  let colors = [];
  let data = [];
  for (let tag in testingCounts) {
    categories.push(TESTING_TAGS[tag].label);
    data.push(testingCounts[tag]);
    colors.push(TESTING_TAGS[tag].color);
  }

  var options = {
    series: [
      {
        name: "Tags",
        data,
      },
    ],
    chart: {
      height: 150,
      type: "bar",
    },
    plotOptions: {
      bar: {
        dataLabels: {
          position: "top", // top, center, bottom
        },
      },
    },

    xaxis: {
      categories,
      // position: "bottom",
      axisBorder: {
        show: false,
      },
      axisTicks: {
        show: false,
      },
    },
    yaxis: {
      axisBorder: {
        show: false,
      },
      axisTicks: {
        show: false,
      },
      labels: {
        show: false,
      },
    },
  };

  let chartEl = document.createElement("div");
  resultSummary.append(chartEl);

  var chart = new ApexCharts(chartEl, options);
  chart.render();
}

async function buildTable() {
  let data = await landingsData;
  let metaBugID = getOption("metaBugID");
  let testingTags = getOption("testingTags");
  let includeUnknown = testingTags.includes("unknown");
  if (testingTags.includes("missing")) {
    testingTags[testingTags.indexOf("missing")] = "none";
  }

  let bugSummaries = [].concat.apply([], Object.values(data));
  if (metaBugID) {
    bugSummaries = bugSummaries.filter((bugSummary) =>
      bugSummary["meta_ids"].includes(Number(metaBugID))
    );
  }

  let startDate = getOption("startDate");
  if (startDate) {
    startDate = Temporal.PlainDate.from(startDate);
    bugSummaries = bugSummaries.filter((bugSummary) => {
      return (
        Temporal.PlainDate.compare(
          Temporal.PlainDate.from(bugSummary.date),
          startDate
        ) >= 0
      );
    });
  }

  let endDate = getOption("endDate");
  if (endDate) {
    endDate = Temporal.PlainDate.from(endDate);
    bugSummaries = bugSummaries.filter((bugSummary) => {
      return (
        Temporal.PlainDate.compare(
          Temporal.PlainDate.from(bugSummary.date),
          endDate
        ) <= 0
      );
    });
  }

  if (testingTags) {
    bugSummaries = bugSummaries.filter((bugSummary) =>
      bugSummary.commits.some((commit) => {
        if (includeUnknown && !commit.testing) {
          return true;
        }
        return commit.testing && testingTags.includes(commit.testing);
      })
    );
  }

  bugSummaries.reverse();
  if (getOption("riskinessEnabled")) {
    // bugSummaries.sort(
    //   (bugSummary1, bugSummary2) => bugSummary2["risk"] - bugSummary1["risk"]
    // );
    document.getElementById("riskinessColumn").style.removeProperty("display");
  } else {
    document.getElementById("riskinessColumn").style.display = "none";
  }

  renderTestingSummary(bugSummaries);

  for (let bugSummary of bugSummaries) {
    addRow(bugSummary);
  }
}

function rebuildTable() {
  let table = document.getElementById("table");
  let summary = resultSummary;
  summary.textContent = "";

  while (table.rows.length > 1) {
    table.deleteRow(table.rows.length - 1);
  }

  buildTable();
}

(function init() {
  buildMetabugsDropdown();

  Object.keys(options).forEach(function (optionName) {
    let optionType = getOptionType(optionName);
    let elem = document.getElementById(optionName);

    if (optionType === "text") {
      setOption(optionName, elem.value);
      elem.addEventListener("change", function () {
        setOption(optionName, elem.value);
        rebuildTable();
      });
    } else if (optionType === "checkbox") {
      setOption(optionName, elem.checked);

      elem.onchange = function () {
        setOption(optionName, elem.checked);
        rebuildTable();
      };
    } else if (optionType === "select") {
      let value = [];
      for (let option of elem.options) {
        if (option.selected) {
          value.push(option.value);
        }
      }

      setOption(optionName, value);

      elem.onchange = function () {
        let value = [];
        for (let option of elem.options) {
          if (option.selected) {
            value.push(option.value);
          }
        }

        setOption(optionName, value);
        rebuildTable();
      };
    } else {
      throw new Error("Unexpected option type.");
    }
  });
  buildTable();
})();
