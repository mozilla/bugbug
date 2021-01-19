// TODO: On click, show previous components affected by similar patches.
// TODO: On click, show previous bugs caused by similar patches.

import localForage from "localforage";
import { Temporal } from "proposal-temporal/lib/index.mjs";
import ApexCharts from "apexcharts";
import {
  TESTING_TAGS,
  featureMetabugs,
  landingsData,
  Counter,
  getSummaryData,
  summarizeCoverage,
} from "./common.js";

localForage.config({
  driver: localForage.INDEXEDDB,
  name: "bugbug-index",
});

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
  whiteBoard: {
    value: null,
    type: "text",
  },
  components: {
    value: null,
    type: "select",
  },
  teams: {
    value: null,
    type: "select",
  },
  grouping: {
    value: null,
    type: "radio",
  },
  releaseVersions: {
    value: null,
    type: "select",
  },
  types: {
    value: null,
    type: "select",
  },
  severities: {
    value: null,
    type: "select",
  },
  riskiness: {
    value: null,
    type: "select",
  },
};

let sortBy = ["Date", "DESC"];
let resultSummary = document.getElementById("result-summary");
let resultGraphs = document.getElementById("result-graphs");
let metabugsDropdown = document.querySelector("#featureMetabugs");
let allBugTypes;

let bugDetails = document.querySelector("#bug-details");
// TODO: port this to an option maybe
async function buildMetabugsDropdown() {
  metabugsDropdown.addEventListener("change", () => {
    setOption("metaBugID", metabugsDropdown.value);
    renderUI();
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

async function buildComponentsSelect() {
  let componentSelect = document.getElementById("components");

  let data = await landingsData;

  let allComponents = new Set();
  for (let landings of Object.values(data)) {
    for (let landing of landings) {
      allComponents.add(landing["component"]);
    }
  }

  let components = [...allComponents];
  components.sort();

  for (let component of components) {
    let option = document.createElement("option");
    option.setAttribute("value", component);
    option.textContent = component;
    option.selected = true;
    componentSelect.append(option);
  }
}

async function buildTeamsSelect() {
  let teamsSelect = document.getElementById("teams");

  let data = await landingsData;

  let allTeams = new Set();
  for (let landings of Object.values(data)) {
    for (let landing of landings) {
      allTeams.add(landing["team"]);
    }
  }

  let teams = [...allTeams];
  teams.sort();

  for (let team of teams) {
    let option = document.createElement("option");
    option.setAttribute("value", team);
    option.textContent = team;
    option.selected = true;
    teamsSelect.append(option);
  }
}

async function buildTypesSelect() {
  let typesSelect = document.getElementById("types");

  let data = await landingsData;

  let types = new Set();
  for (let landings of Object.values(data)) {
    for (let landing of landings) {
      if (landing.types.length) {
        types.add(...landing.types);
      }
    }
  }

  types.add("unknown");

  allBugTypes = [...types];

  for (let type of allBugTypes) {
    let option = document.createElement("option");
    option.setAttribute("value", type);
    option.textContent = type;
    option.selected = true;
    typesSelect.append(option);
  }
}

async function buildSeveritiesSelect() {
  let severitiesSelect = document.getElementById("severities");

  let data = await landingsData;

  let allSeverities = new Set();
  for (let landings of Object.values(data)) {
    for (let landing of landings) {
      allSeverities.add(landing.severity);
    }
  }

  let severities = [...allSeverities];

  for (let type of severities) {
    let option = document.createElement("option");
    option.setAttribute("value", type);
    option.textContent = type;
    option.selected = true;
    severitiesSelect.append(option);
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
        document.createTextNode(TESTING_TAGS[commit.testing].label)
      );
    }
    testing_tags_list.append(testing_tags_list_item);
  }
  testing_tags_column.append(testing_tags_list);

  let coverage_column = row.insertCell(3);
  let [lines_added, lines_covered, lines_unknown] = summarizeCoverage(
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

async function populateVersions() {
  var versionSelector = document.getElementById("releaseVersions");

  let data = await landingsData;

  var allVersions = new Set();
  for (let bugs of Object.values(data)) {
    bugs.forEach((item) => {
      if (item.versions.length) {
        allVersions.add(...item.versions);
      }
    });
  }
  var versions = [...allVersions];
  versions.sort();

  for (let version of versions) {
    let el = document.createElement("option");
    el.setAttribute("value", version);
    el.textContent = version;
    el.selected = false;
    versionSelector.appendChild(el);
  }

  // For now, previous two releases by default:
  versionSelector.lastChild.selected = true;
  versionSelector.lastChild.previousSibling.selected = true;
}

function renderTestingChart(chartEl, bugSummaries) {
  let testingCounts = new Counter();
  bugSummaries.forEach((summary) => {
    summary.commits.forEach((commit) => {
      if (!commit.testing) {
        testingCounts.unknown++;
      } else {
        testingCounts[commit.testing] = testingCounts[commit.testing] + 1;
      }
    });
  });

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

  var chart = new ApexCharts(chartEl, options);
  chart.render();
}

async function renderRiskChart(chartEl, bugSummaries) {
  bugSummaries = bugSummaries.filter(
    (bugSummary) => bugSummary.risk_band !== null
  );

  if (bugSummaries.length == 0) {
    return;
  }

  let minDate = Temporal.PlainDate.from(
    bugSummaries.reduce((minSummary, summary) =>
      Temporal.PlainDate.compare(
        Temporal.PlainDate.from(summary.date),
        Temporal.PlainDate.from(minSummary.date)
      ) < 0
        ? summary
        : minSummary
    ).date
  );

  // Enforce up to 2 months history, earlier patches are in the model's training set.
  let twoMonthsAgo = Temporal.now
    .plainDateISO()
    .subtract(new Temporal.Duration(0, 2));
  if (Temporal.PlainDate.compare(twoMonthsAgo, minDate) < 0) {
    minDate = twoMonthsAgo;
  }

  let summaryData = await getSummaryData(
    bugSummaries,
    getOption("grouping"),
    minDate,
    (counterObj, summary) => {
      if (summary.risk_band == "l") {
        counterObj.low += 1;
      } else if (summary.risk_band == "a") {
        counterObj.medium += 1;
      } else {
        counterObj.high += 1;
      }
    }
  );

  let categories = [];
  let high = [];
  let medium = [];
  let low = [];
  for (let date in summaryData) {
    categories.push(date);
    low.push(summaryData[date].low);
    medium.push(summaryData[date].medium);
    high.push(summaryData[date].high);
  }

  let options = {
    series: [
      {
        name: "Higher",
        data: high,
      },
      {
        name: "Average",
        data: medium,
      },
      {
        name: "Lower",
        data: low,
      },
    ],
    chart: {
      height: 350,
      type: "line",
      dropShadow: {
        enabled: true,
        color: "#000",
        top: 18,
        left: 7,
        blur: 10,
        opacity: 0.2,
      },
      toolbar: {
        show: false,
      },
    },
    colors: [HIGH_RISK_COLOR, MEDIUM_RISK_COLOR, LOW_RISK_COLOR],
    dataLabels: {
      enabled: true,
    },
    stroke: {
      curve: "smooth",
    },
    title: {
      text: "Evolution of lower/average/higher risk changes",
      align: "left",
    },
    grid: {
      borderColor: "#e7e7e7",
      row: {
        colors: ["#f3f3f3", "transparent"],
        opacity: 0.5,
      },
    },
    markers: {
      size: 1,
    },
    xaxis: {
      categories: categories,
      title: {
        text: "Date",
      },
    },
    yaxis: {
      title: {
        text: "# of patches",
      },
    },
    legend: {
      position: "top",
      horizontalAlign: "right",
      floating: true,
      offsetY: -25,
      offsetX: -5,
    },
  };

  let chart = new ApexCharts(chartEl, options);
  chart.render();
}

function filterByCreationDate(bugSummaries) {
  let startDate = getOption("startDate");
  if (startDate) {
    startDate = Temporal.PlainDate.from(startDate);
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        Temporal.PlainDate.compare(
          Temporal.PlainDate.from(bugSummary.creation_date),
          startDate
        ) >= 0
    );
  }

  let endDate = getOption("endDate");
  if (endDate) {
    endDate = Temporal.PlainDate.from(endDate);
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        Temporal.PlainDate.compare(
          Temporal.PlainDate.from(bugSummary.creation_date),
          endDate
        ) <= 0
    );
  }

  return bugSummaries;
}

async function renderRegressionsChart(chartEl, bugSummaries) {
  bugSummaries = filterByCreationDate(bugSummaries);

  if (bugSummaries.length == 0) {
    return;
  }

  let minDate = Temporal.PlainDate.from(
    bugSummaries.reduce((minSummary, summary) =>
      Temporal.PlainDate.compare(
        Temporal.PlainDate.from(summary.creation_date),
        Temporal.PlainDate.from(minSummary.creation_date)
      ) < 0
        ? summary
        : minSummary
    ).creation_date
  );

  let summaryData = await getSummaryData(
    bugSummaries,
    getOption("grouping"),
    minDate,
    (counterObj, bug) => {
      if (bug.regression) {
        counterObj.regressions += 1;
      }
    },
    null,
    (summary) => summary.creation_date
  );

  let categories = [];
  let regressions = [];
  for (let date in summaryData) {
    categories.push(date);
    regressions.push(summaryData[date].regressions);
  }

  let options = {
    series: [
      {
        name: "Regressions",
        data: regressions,
      },
    ],
    chart: {
      height: 350,
      type: "line",
      dropShadow: {
        enabled: true,
        color: "#000",
        top: 18,
        left: 7,
        blur: 10,
        opacity: 0.2,
      },
      toolbar: {
        show: false,
      },
    },
    dataLabels: {
      enabled: true,
    },
    stroke: {
      curve: "smooth",
    },
    title: {
      text: "Number of regressions",
      align: "left",
    },
    grid: {
      borderColor: "#e7e7e7",
      row: {
        colors: ["#f3f3f3", "transparent"],
        opacity: 0.5,
      },
    },
    markers: {
      size: 1,
    },
    xaxis: {
      categories: categories,
      title: {
        text: "Date",
      },
    },
    yaxis: {
      title: {
        text: "# of regressions",
      },
    },
    legend: {
      position: "top",
      horizontalAlign: "right",
      floating: true,
      offsetY: -25,
      offsetX: -5,
    },
  };

  let chart = new ApexCharts(chartEl, options);
  chart.render();
}

async function renderTypesChart(chartEl, bugSummaries) {
  bugSummaries = filterByCreationDate(bugSummaries);

  if (bugSummaries.length == 0) {
    return;
  }

  let minDate = Temporal.PlainDate.from(
    bugSummaries.reduce((minSummary, summary) =>
      Temporal.PlainDate.compare(
        Temporal.PlainDate.from(summary.creation_date),
        Temporal.PlainDate.from(minSummary.creation_date)
      ) < 0
        ? summary
        : minSummary
    ).creation_date
  );

  let summaryData = await getSummaryData(
    bugSummaries,
    getOption("grouping"),
    minDate,
    (counterObj, bug) => {
      for (const type of bug.types) {
        counterObj[type] += 1;
      }
    },
    null,
    (summary) => summary.creation_date
  );

  let all_series = [];
  for (let type of allBugTypes) {
    all_series.push({
      name: type,
      data: [],
    });
  }

  let categories = [];
  for (let date in summaryData) {
    categories.push(date);
    for (let series of all_series) {
      series["data"].push(summaryData[date][series["name"]]);
    }
  }

  let options = {
    series: all_series,
    chart: {
      height: 350,
      type: "line",
      dropShadow: {
        enabled: true,
        color: "#000",
        top: 18,
        left: 7,
        blur: 10,
        opacity: 0.2,
      },
      toolbar: {
        show: false,
      },
    },
    dataLabels: {
      enabled: true,
    },
    stroke: {
      curve: "smooth",
    },
    title: {
      text: "Number of bugs by type",
      align: "left",
    },
    grid: {
      borderColor: "#e7e7e7",
      row: {
        colors: ["#f3f3f3", "transparent"],
        opacity: 0.5,
      },
    },
    markers: {
      size: 1,
    },
    xaxis: {
      categories: categories,
      title: {
        text: "Date",
      },
    },
    yaxis: {
      title: {
        text: "# of bugs",
      },
    },
    legend: {
      position: "top",
      horizontalAlign: "right",
      floating: true,
      offsetY: -25,
      offsetX: -5,
    },
  };

  let chart = new ApexCharts(chartEl, options);
  chart.render();
}

async function renderFixTimesChart(chartEl, bugSummaries) {
  bugSummaries = bugSummaries.filter((bugSummary) => bugSummary.date !== null);

  bugSummaries = filterByCreationDate(bugSummaries);

  if (bugSummaries.length == 0) {
    return;
  }

  let minDate = Temporal.PlainDate.from(
    bugSummaries.reduce((minSummary, summary) =>
      Temporal.PlainDate.compare(
        Temporal.PlainDate.from(summary.creation_date),
        Temporal.PlainDate.from(minSummary.creation_date)
      ) < 0
        ? summary
        : minSummary
    ).creation_date
  );

  let summaryData = await getSummaryData(
    bugSummaries,
    getOption("grouping"),
    minDate,
    (counterObj, bug) => {
      counterObj.fix_time += Temporal.PlainDate.from(
        bug.creation_date
      ).until(Temporal.PlainDate.from(bug.date), { largestUnit: "days" }).days;
      counterObj.bugs += 1;
    },
    null,
    (summary) => summary.creation_date
  );

  let categories = [];
  let average_fix_times = [];
  for (let date in summaryData) {
    categories.push(date);
    average_fix_times.push(
      Math.ceil(summaryData[date].fix_time / summaryData[date].bugs)
    );
  }

  let options = {
    series: [
      {
        name: "Average fix times",
        data: average_fix_times,
      },
    ],
    chart: {
      height: 350,
      type: "line",
      dropShadow: {
        enabled: true,
        color: "#000",
        top: 18,
        left: 7,
        blur: 10,
        opacity: 0.2,
      },
      toolbar: {
        show: false,
      },
    },
    dataLabels: {
      enabled: true,
    },
    stroke: {
      curve: "smooth",
    },
    title: {
      text: "Average fix times",
      align: "left",
    },
    grid: {
      borderColor: "#e7e7e7",
      row: {
        colors: ["#f3f3f3", "transparent"],
        opacity: 0.5,
      },
    },
    markers: {
      size: 1,
    },
    xaxis: {
      categories: categories,
      title: {
        text: "Date",
      },
    },
    yaxis: {
      title: {
        text: "Average fix times",
      },
    },
    legend: {
      position: "top",
      horizontalAlign: "right",
      floating: true,
      offsetY: -25,
      offsetX: -5,
    },
  };

  let chart = new ApexCharts(chartEl, options);
  chart.render();
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
  let metaBugID = getOption("metaBugID");

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
  renderTestingChart(testingChartEl, bugSummaries);

  let riskChartEl = document.createElement("div");
  resultGraphs.append(riskChartEl);
  await renderRiskChart(riskChartEl, bugSummaries);

  let regressionsChartEl = document.createElement("div");
  resultGraphs.append(regressionsChartEl);
  await renderRegressionsChart(regressionsChartEl, bugSummaries);

  let typesChartEl = document.createElement("div");
  resultGraphs.append(typesChartEl);
  await renderTypesChart(typesChartEl, bugSummaries);

  let fixTimesChartEl = document.createElement("div");
  resultGraphs.append(fixTimesChartEl);
  await renderFixTimesChart(fixTimesChartEl, bugSummaries);
}

async function renderUI(rerenderSummary = true) {
  let data = await landingsData;
  let metaBugID = getOption("metaBugID");
  let testingTags = getOption("testingTags");
  let components = getOption("components");
  let teams = getOption("teams");
  let whiteBoard = getOption("whiteBoard");
  let releaseVersions = getOption("releaseVersions");
  let types = getOption("types");
  let severities = getOption("severities");
  let riskiness = getOption("riskiness");

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
          Temporal.PlainDate.from(
            bugSummary.date ? bugSummary.date : bugSummary.creation_date
          ),
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
          Temporal.PlainDate.from(
            bugSummary.date ? bugSummary.date : bugSummary.creation_date
          ),
          endDate
        ) <= 0
      );
    });
  }

  if (testingTags) {
    const includeUnknownTestingTags = testingTags.includes("unknown");
    const includeNotAvailableTestingTags = releaseVersions.includes("N/A");
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        (includeNotAvailableTestingTags && bugSummary.commits.length == 0) ||
        bugSummary.commits.some(
          (commit) =>
            (includeUnknownTestingTags && !commit.testing) ||
            testingTags.includes(commit.testing)
        )
    );
  }

  if (components) {
    bugSummaries = bugSummaries.filter((bugSummary) =>
      components.includes(bugSummary["component"])
    );
  }

  if (teams) {
    bugSummaries = bugSummaries.filter((bugSummary) =>
      teams.includes(bugSummary["team"])
    );
  }

  if (whiteBoard) {
    bugSummaries = bugSummaries.filter((bugSummary) =>
      bugSummary["whiteboard"].includes(whiteBoard)
    );
  }

  if (releaseVersions) {
    const includeNotAvailableVersion = releaseVersions.includes("N/A");
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        (includeNotAvailableVersion && bugSummary.versions.length == 0) ||
        releaseVersions.some((version) =>
          bugSummary.versions.includes(Number(version))
        )
    );
  }

  if (types) {
    if (!types.includes("unknown")) {
      bugSummaries = bugSummaries.filter((bugSummary) =>
        bugSummary.types.some((type) => types.includes(type))
      );
    }
  }

  if (severities) {
    bugSummaries = bugSummaries.filter((bugSummary) =>
      severities.includes(bugSummary.severity)
    );
  }

  if (riskiness) {
    const includeNotAvailableRiskiness = riskiness.includes("N/A");
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        (includeNotAvailableRiskiness && bugSummary.risk_band === null) ||
        riskiness.includes(bugSummary.risk_band)
    );
  }

  let sortFunction = null;
  if (sortBy[0] == "Date") {
    sortFunction = function (a, b) {
      return Temporal.PlainDate.compare(
        Temporal.PlainDate.from(a.date ? a.date : a.creation_date),
        Temporal.PlainDate.from(b.date ? b.date : b.creation_date)
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
      let [lines_added_a, lines_covered_a, lines_unknown_a] = summarizeCoverage(
        a
      );
      let [lines_added_b, lines_covered_b, lines_unknown_b] = summarizeCoverage(
        b
      );

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
  await buildComponentsSelect();
  await buildTeamsSelect();
  await populateVersions();
  await buildTypesSelect();
  await buildSeveritiesSelect();

  setTableHeaderHandlers();

  Object.keys(options).forEach(function (optionName) {
    let optionType = getOptionType(optionName);
    let elem = document.getElementById(optionName);

    if (optionType === "text") {
      setOption(optionName, elem.value);
      elem.addEventListener("change", function () {
        setOption(optionName, elem.value);
        renderUI();
      });
    } else if (optionType === "checkbox") {
      setOption(optionName, elem.checked);

      elem.onchange = function () {
        setOption(optionName, elem.checked);
        renderUI();
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
        renderUI();
      };
    } else if (optionType === "radio") {
      for (const radio of document.querySelectorAll(
        `input[name=${optionName}]`
      )) {
        if (radio.checked) {
          setOption(optionName, radio.value);
        }
      }

      elem.onchange = function () {
        for (const radio of document.querySelectorAll(
          `input[name=${optionName}]`
        )) {
          if (radio.checked) {
            setOption(optionName, radio.value);
          }
        }
        renderUI();
      };
    } else {
      throw new Error("Unexpected option type.");
    }
  });

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
