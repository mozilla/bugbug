import ApexCharts from "apexcharts";
import { Temporal } from "proposal-temporal/lib/index.mjs";
import localForage from "localforage";

// let METABUGS_URL =
//   "https://bugzilla.mozilla.org/rest/bug?include_fields=id,summary,status&keywords=feature-testing-meta%2C%20&keywords_type=allwords";
let LANDINGS_URL =
  "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.landings_risk_report.latest/artifacts/public/landings_by_date.json";
let COMPONENT_CONNECTIONS_URL =
  "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.landings_risk_report.latest/artifacts/public/component_connections.json";

function getCSSVariableValue(name) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

const EXPIRE_CACHE = (() => {
  localForage.config({
    driver: localForage.INDEXEDDB,
  });
  return {
    get: async (key) => {
      let data;
      try {
        data = await localForage.getItem(key);
      } catch (e) {}

      if (!data) return data;

      const { expire, value } = data;

      if (expire < Date.now()) {
        localForage.removeItem(key);
        return null;
      }

      return value;
    },
    set: (key, value, expire = false, callback = false) => {
      if (expire && typeof expire === "number")
        expire = Math.round(expire * 1000 + Date.now()); // * 1000 to use seconds

      return localForage.setItem(key, { value, expire }, expire && callback);
    },
  };
})();

export let getPlainDate = (() => {
  let cache = new Map();

  return (date) => {
    let plainDate = cache.get(date);
    if (!plainDate) {
      plainDate = Temporal.PlainDate.from(date);
      cache.set(date, plainDate);
    }

    return plainDate;
  };
})();

export const TESTING_TAGS = {
  "testing-approved": {
    color: getCSSVariableValue("--green-60"),
    label: "approved",
  },
  "testing-exception-unchanged": {
    color: getCSSVariableValue("--teal-60"),
    label: "unchanged",
  },
  "testing-exception-elsewhere": {
    color: getCSSVariableValue("--blue-50"),
    label: "elsewhere",
  },
  "testing-exception-ui": {
    color: getCSSVariableValue("--purple-50"),
    label: "ui",
  },
  "testing-exception-other": {
    color: getCSSVariableValue("--yellow-50"),
    label: "other",
  },
  missing: {
    color: getCSSVariableValue("--red-60"),
    label: "missing",
  },
  unknown: {
    color: getCSSVariableValue("--grey-30"),
    label: "unknown",
  },
};

let taskclusterLandingsArtifact = (async function () {
  let json = await EXPIRE_CACHE.get("taskclusterLandingsArtifact");
  if (!json) {
    let response = await fetch(LANDINGS_URL);
    json = await response.json();
    // 30 minutes
    EXPIRE_CACHE.set("taskclusterLandingsArtifact", json, 60 * 30);
  } else {
    console.log("taskclusterLandingsArtifact cache hit", json);
  }

  return json;
})();

let taskclusterComponentConnectionsArtifact = (async function () {
  let json = await EXPIRE_CACHE.get("taskclusterComponentConnectionsArtifact");
  if (!json) {
    let response = await fetch(COMPONENT_CONNECTIONS_URL);
    json = await response.json();
    // 30 minutes
    EXPIRE_CACHE.set("taskclusterComponentConnectionsArtifact", json, 60 * 30);
  } else {
    console.log("taskclusterComponentConnectionsArtifact cache hit", json);
  }

  return json;
})();

export let componentConnections = (async function () {
  let json = await taskclusterComponentConnectionsArtifact;
  return json;
})();

export let featureMetabugs = (async function () {
  let json = await taskclusterLandingsArtifact;
  return json.featureMetaBugs;
})();

export async function getFirefoxReleases() {
  let response = await fetch(
    "https://product-details.mozilla.org/1.0/firefox_history_major_releases.json"
  );
  return await response.json();
}

export let landingsData = (async function () {
  let json = await taskclusterLandingsArtifact;
  json = json.summaries;

  // Sort the dates so object iteration will be sequential:
  let orderedDates = [];
  for (let date in json) {
    orderedDates.push(date);
  }
  orderedDates.sort((a, b) => {
    return Temporal.PlainDate.compare(getPlainDate(a), getPlainDate(b));
  });

  let returnedObject = {};
  for (let date of orderedDates) {
    returnedObject[date] = json[date];
  }

  document.body.classList.remove("loading-data");

  return returnedObject;
})();

export class Counter {
  constructor() {
    return new Proxy(
      {},
      {
        get: (target, name) => (name in target ? target[name] : 0),
      }
    );
  }
}

export async function getSummaryData(
  bugSummaries,
  grouping = "daily",
  startDate,
  counter,
  filter,
  dateGetter = (summary) => summary.date
) {
  let dates = [...new Set(bugSummaries.map((summary) => dateGetter(summary)))];
  dates.sort((a, b) =>
    Temporal.PlainDate.compare(getPlainDate(a), getPlainDate(b))
  );

  let dailyData = {};
  for (let date of dates) {
    if (Temporal.PlainDate.compare(getPlainDate(date), startDate) < 1) {
      continue;
    }

    dailyData[date] = new Counter();
  }

  for (let summary of bugSummaries) {
    let counterObj = dailyData[dateGetter(summary)];
    if (!counterObj) {
      continue;
    }

    if (filter && !filter(summary)) {
      continue;
    }

    counter(counterObj, summary);
  }

  let labels = new Set(
    Object.values(dailyData).flatMap((data) => Object.keys(data))
  );

  if (grouping == "weekly") {
    let weeklyData = {};
    for (let daily in dailyData) {
      let date = getPlainDate(daily);
      let weekStart = date.subtract({ days: date.dayOfWeek }).toString();

      if (!weeklyData[weekStart]) {
        weeklyData[weekStart] = new Counter();
      }

      for (let label of labels) {
        weeklyData[weekStart][label] += dailyData[daily][label];
      }
    }

    return weeklyData;
  } else if (grouping == "monthly") {
    let monthlyData = {};
    for (let daily in dailyData) {
      let date = getPlainDate(daily);
      let yearMonth = Temporal.PlainYearMonth.from(date);

      if (!monthlyData[yearMonth]) {
        monthlyData[yearMonth] = new Counter();
      }

      for (let label of labels) {
        monthlyData[yearMonth][label] += dailyData[daily][label];
      }
    }
    return monthlyData;
  } else if (grouping == "by_release") {
    let byReleaseData = {};
    let releases = await getFirefoxReleases();
    for (const daily in dailyData) {
      let version = null;
      for (const [cur_version, cur_date] of Object.entries(releases)) {
        if (
          Temporal.PlainDate.compare(
            getPlainDate(daily),
            getPlainDate(cur_date)
          ) < 1
        ) {
          break;
        }
        version = cur_version;
      }

      if (!byReleaseData[version]) {
        byReleaseData[version] = new Counter();
      }

      for (let label of labels) {
        byReleaseData[version][label] += dailyData[daily][label];
      }
    }
    return byReleaseData;
  }

  return dailyData;
}

export async function getTestingPolicySummaryData(grouping = "daily", filter) {
  let bugSummaries = [].concat
    .apply([], Object.values(await landingsData))
    .filter((summary) => summary.date);

  return getSummaryData(
    bugSummaries,
    grouping,
    getPlainDate("2020-09-01"), // Ignore data before the testing policy took place.
    (counterObj, bug) => {
      for (let commit of bug.commits) {
        if (!commit.testing) {
          counterObj.unknown++;
        } else {
          counterObj[commit.testing] += 1;
        }
      }
    },
    filter
  );
}

export function renderChart(chartEl, series, dates, title, yaxis_text) {
  let options = {
    series: series,
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
      text: title,
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
      categories: dates,
      title: {
        text: "Date",
      },
    },
    yaxis: {
      title: {
        text: yaxis_text,
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

export function summarizeCoverage(bugSummary) {
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

  return [lines_added, lines_covered, lines_unknown];
}

export async function getComponentRegressionMap(threshold = 0.05) {
  let connections = await componentConnections;

  // // Return an object with each component and the components that are most likely
  // // to cause regressions to that component.
  let connectionsMap = {};
  for (let c of connections) {
    for (let regression_component in c.most_common_regression_components) {
      // Ignore < N%
      if (
        c.most_common_regression_components[regression_component] < threshold
      ) {
        continue;
      }
      if (!connectionsMap[regression_component]) {
        connectionsMap[regression_component] = {};
      }
      connectionsMap[regression_component][c.component] =
        c.most_common_regression_components[regression_component];
    }
  }

  return connectionsMap;
}

let options = {
  metaBugID: {
    value: null,
    type: "text",
  },
  testingTags: {
    value: null,
    type: "select",
  },
  fixStartDate: {
    value: null,
    type: "text",
  },
  fixEndDate: {
    value: null,
    type: "text",
  },
  createStartDate: {
    value: null,
    type: "text",
  },
  createEndDate: {
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
  includeUnfixed: {
    value: null,
    type: "checkbox",
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

export function getOption(name) {
  return options[name].value;
}

function getOptionType(name) {
  return options[name].type;
}

export function setOption(name, value) {
  return (options[name].value = value);
}

async function buildComponentsSelect() {
  let componentSelect = document.getElementById("components");
  if (!componentSelect) {
    return;
  }

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
  if (!teamsSelect) {
    return;
  }

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

async function populateVersions() {
  var versionSelector = document.getElementById("releaseVersions");
  if (!versionSelector) {
    return;
  }

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
    versionSelector.prepend(el);
  }

  // For now, previous two releases by default:
  versionSelector.firstChild.selected = true;
  versionSelector.firstChild.nextSibling.selected = true;
}

export let allBugTypes;

async function buildTypesSelect() {
  let typesSelect = document.getElementById("types");
  if (!typesSelect) {
    return;
  }

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
  if (!severitiesSelect) {
    return;
  }

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

export async function setupOptions(callback) {
  await buildComponentsSelect();
  await buildTeamsSelect();
  await populateVersions();
  await buildTypesSelect();
  await buildSeveritiesSelect();

  Object.keys(options).forEach(function (optionName) {
    let optionType = getOptionType(optionName);
    let elem = document.getElementById(optionName);
    if (!elem) {
      return;
    }

    if (optionType === "text") {
      setOption(optionName, elem.value);
      elem.addEventListener("change", function () {
        setOption(optionName, elem.value);
        callback();
      });
    } else if (optionType === "checkbox") {
      setOption(optionName, elem.checked);

      elem.onchange = function () {
        setOption(optionName, elem.checked);
        callback();
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
        callback();
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
        callback();
      };
    } else {
      throw new Error("Unexpected option type.");
    }
  });
}

export async function getFilteredBugSummaries() {
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

  let fixStartDate = getOption("fixStartDate");
  if (fixStartDate) {
    fixStartDate = Temporal.PlainDate.from(fixStartDate);
    bugSummaries = bugSummaries.filter((bugSummary) => {
      if (!bugSummary.date) {
        return false;
      }

      return (
        Temporal.PlainDate.compare(
          getPlainDate(bugSummary.date),
          fixStartDate
        ) >= 0
      );
    });
  }

  let fixEndDate = getOption("fixEndDate");
  if (fixEndDate) {
    fixEndDate = Temporal.PlainDate.from(fixEndDate);
    bugSummaries = bugSummaries.filter((bugSummary) => {
      if (!bugSummary.date) {
        return false;
      }

      return (
        Temporal.PlainDate.compare(getPlainDate(bugSummary.date), fixEndDate) <=
        0
      );
    });
  }

  let createStartDate = getOption("createStartDate");
  if (createStartDate) {
    createStartDate = Temporal.PlainDate.from(createStartDate);
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        Temporal.PlainDate.compare(
          getPlainDate(bugSummary.creation_date),
          createStartDate
        ) >= 0
    );
  }

  let createEndDate = getOption("createEndDate");
  if (createEndDate) {
    createEndDate = Temporal.PlainDate.from(createEndDate);
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        Temporal.PlainDate.compare(
          getPlainDate(bugSummary.creation_date),
          createEndDate
        ) <= 0
    );
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
    const includeUnfixed = getOption("includeUnfixed");
    bugSummaries = bugSummaries.filter(
      (bugSummary) =>
        (includeUnfixed && bugSummary.versions.length == 0) ||
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

  return bugSummaries;
}
