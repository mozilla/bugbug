// TODO: On click, show previous components affected by similar patches.
// TODO: On click, show previous bugs caused by similar patches.

import localForage from "localforage";
import { Temporal } from "proposal-temporal/lib/index.mjs";
import * as common from "./common.js";

localForage.config({
  driver: localForage.INDEXEDDB,
  name: "bugbug-index",
});

const HIGH_RISK_COLOR = "rgb(255, 13, 87)";
const MEDIUM_RISK_COLOR = "darkkhaki";
const LOW_RISK_COLOR = "green";

let sortBy = ["Date", "DESC"];
let resultSummary = document.getElementById("result-summary");
let resultGraphs = document.getElementById("result-graphs");
let metabugsDropdown = document.querySelector("#featureMetabugs");

let bugDetails = document.querySelector("#bug-details");
// TODO: port this to an option maybe
async function buildMetabugsDropdown() {
  metabugsDropdown.addEventListener("change", () => {
    setOption("metaBugID", metabugsDropdown.value);
    renderUI();
  });
  let bugs = await common.featureMetabugs;
  metabugsDropdown.innerHTML = `<option value="" selected>Choose a feature metabug</option>`;
  for (let bug of bugs) {
    let option = document.createElement("option");
    option.setAttribute("value", bug.id);
    option.textContent = bug.summary;
    metabugsDropdown.append(option);
  }
}

function addRow(bugSummary) {
  let table = document.getElementById("table");

  let row = table.insertRow(table.rows.length);

  let bug_column = row.insertCell(0);
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

  let date_column = row.insertCell(1);
  date_column.textContent = bugSummary.date;

  let testing_tags_column = row.insertCell(2);
  testing_tags_column.classList.add("testing-tags");
  let testing_tags_list = document.createElement("ul");
  for (let commit of bugSummary.commits) {
    let testing_tags_list_item = document.createElement("li");
    if (!commit.testing) {
      testing_tags_list_item.append(document.createTextNode("unknown"));
    } else {
      testing_tags_list_item.append(
        document.createTextNode(common.TESTING_TAGS[commit.testing].label)
      );
    }
    testing_tags_list.append(testing_tags_list_item);
  }
  testing_tags_column.append(testing_tags_list);

  let coverage_column = row.insertCell(3);
  let [lines_added, lines_covered, lines_unknown] = common.summarizeCoverage(
    bugSummary
  );
  if (lines_added != 0) {
    if (lines_unknown != 0) {
      coverage_column.textContent = `${lines_covered}-${
        lines_covered + lines_unknown
      } of ${lines_added}`;
    } else {
      coverage_column.textContent = `${lines_covered} of ${lines_added}`;
    }
  } else {
    coverage_column.textContent = "";
  }

  let risk_list = document.createElement("ul");
  let risk_column = row.insertCell(4);

  let risk_text = document.createElement("span");
  risk_text.textContent = `${bugSummary.risk_band} risk`;
  if (bugSummary.risk_band == "l") {
    // Lower than average risk.
    risk_text.style.color = LOW_RISK_COLOR;
    risk_text.textContent = "Lower";
  } else if (bugSummary.risk_band == "a") {
    // Average risk.
    risk_text.style.color = MEDIUM_RISK_COLOR;
    risk_text.textContent = "Average";
  } else if (bugSummary.risk_band == "h") {
    // Higher than average risk.
    risk_text.style.color = HIGH_RISK_COLOR;
    risk_text.textContent = "Higher";
  } else if (bugSummary.risk_band == null) {
    // No risk available (there are no commits associated to the bug).
    risk_text.textContent = "N/A";
  } else {
    throw new Exception("Unknown risk band");
  }

  risk_column.append(risk_text);
}

async function renderTable(bugSummaries) {
  let table = document.getElementById("table");
  while (table.rows.length > 1) {
    table.deleteRow(table.rows.length - 1);
  }
  for (let bugSummary of bugSummaries.filter((summary) => summary.date)) {
    addRow(bugSummary);
  }
}

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

  let typesChartEl = document.createElement("div");
  resultGraphs.append(typesChartEl);
  await common.renderTypesChart(typesChartEl, bugSummaries);

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

  let sortFunction = null;
  if (sortBy[0] == "Date") {
    sortFunction = function (a, b) {
      return Temporal.PlainDate.compare(
        common.getPlainDate(a.date ? a.date : a.creation_date),
        common.getPlainDate(b.date ? b.date : b.creation_date)
      );
    };
  } else if (sortBy[0] == "Riskiness") {
    sortFunction = function (a, b) {
      if (a.risk_band == b.risk_band) {
        return 0;
      } else if (
        a.risk_band == "h" ||
        (a.risk_band == "a" && b.risk_band == "l")
      ) {
        return 1;
      } else {
        return -1;
      }
    };
  } else if (sortBy[0] == "Bug") {
    sortFunction = function (a, b) {
      return a.id - b.id;
    };
  } else if (sortBy[0] == "Coverage") {
    sortFunction = function (a, b) {
      let [
        lines_added_a,
        lines_covered_a,
        lines_unknown_a,
      ] = common.summarizeCoverage(a);
      let [
        lines_added_b,
        lines_covered_b,
        lines_unknown_b,
      ] = common.summarizeCoverage(b);

      let uncovered_a = lines_added_a - (lines_covered_a + lines_unknown_a);
      let uncovered_b = lines_added_b - (lines_covered_b + lines_unknown_b);

      if (uncovered_a == uncovered_b) {
        return lines_added_a - lines_added_b;
      }

      return uncovered_a - uncovered_b;
    };
  }

  if (sortFunction) {
    if (sortBy[1] == "DESC") {
      bugSummaries.sort((a, b) => -sortFunction(a, b));
    } else {
      bugSummaries.sort(sortFunction);
    }
  }

  if (rerenderSummary) {
    await renderSummary(bugSummaries);
  }

  if (bugDetails.open) {
    await renderTable(bugSummaries);
  }
}

function setTableHeaderHandlers() {
  const table = document.getElementById("table");
  const elems = table.querySelectorAll("th");
  for (let elem of elems) {
    elem.onclick = function () {
      if (sortBy[0] == elem.textContent) {
        if (sortBy[1] == "DESC") {
          sortBy[1] = "ASC";
        } else if (sortBy[1] == "ASC") {
          sortBy[1] = "DESC";
        }
      } else {
        sortBy[0] = elem.textContent;
        sortBy[1] = "DESC";
      }
      renderUI(false);
    };
  }
}

(async function init() {
  buildMetabugsDropdown();

  setTableHeaderHandlers();

  await common.setupOptions(renderUI);

  let toggle = await localForage.getItem("detailsToggle");
  if (toggle) {
    bugDetails.open = true;
  }
  bugDetails.addEventListener("toggle", async () => {
    await localForage.setItem("detailsToggle", bugDetails.open);
    renderUI(false);
  });

  renderUI();
})();
