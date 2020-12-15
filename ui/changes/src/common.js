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
  none: {
    color: getCSSVariableValue("--red-60"),
    label: "missing",
  },
  unknown: {
    color: getCSSVariableValue("--grey-30"),
    label: "unknown",
  }
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
  let response = await fetch("https://product-details.mozilla.org/1.0/firefox_history_major_releases.json");
  return await response.json();
}

export let landingsData = (async function () {
  let json = await taskclusterLandingsArtifact;
  json = json.landings;

  // Sort the dates so object iteration will be sequential:
  let orderedDates = [];
  for (let date in json) {
    orderedDates.push(date);
  }
  orderedDates.sort((a, b) => {
    return Temporal.PlainDate.compare(
      Temporal.PlainDate.from(a),
      Temporal.PlainDate.from(b)
    );
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
        get: (target, name) => (name in target ? target[name] : 0)
      }
    );
  }
}

export async function getSummaryData(
  bugSummaries,
  grouping = "daily",
  startDate,
  counter,
  filter
) {
  let dates = new Set(bugSummaries.map(summary => summary.date));

  let dailyData = {};
  for (let date of dates) {
    if (
      Temporal.PlainDate.compare(Temporal.PlainDate.from(date), startDate) < 1
    ) {
      continue;
    }

    dailyData[date] = new Counter();
  }

  for (let summary of bugSummaries) {
    let counterObj = dailyData[summary.date];
    if (!counterObj) {
      continue;
    }

    if (filter && !filter(summary)) {
      continue;
    }

    counter(counterObj, summary);
  }

  let labels = new Set(
    Object.values(dailyData).flatMap(data => Object.keys(data))
  );

  if (grouping == "weekly") {
    let weeklyData = {};
    for (let daily in dailyData) {
      let date = Temporal.PlainDate.from(daily);
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
      let date = Temporal.PlainDate.from(daily);
      let yearMonth = Temporal.PlainYearMonth.from(date);

      if (!monthlyData[yearMonth]) {
        monthlyData[yearMonth] = new Counter();
      }

      for (let label of labels) {
        monthlyData[yearMonth][label] += dailyData[daily][label];
      }
    }
    return monthlyData;
  } else if (grouping = "by_release") {
    let byReleaseData = {};
    let releases = await getFirefoxReleases();
    for (const daily in dailyData) {
      let version = null;
      for (const [cur_version, cur_date] of Object.entries(releases)) {
        if (Temporal.PlainDate.compare(Temporal.PlainDate.from(daily), Temporal.PlainDate.from(cur_date)) < 1) {
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
  let bugSummaries = [].concat.apply([], Object.values(await landingsData));

  return getSummaryData(
    bugSummaries,
    grouping,
    Temporal.PlainDate.from("2020-09-01"), // Ignore data before the testing policy took place.
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
