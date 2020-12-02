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

export function getNewTestingTagCountObject() {
  let obj = {};
  for (let tag in TESTING_TAGS) {
    obj[tag] = 0;
  }
  return obj;
}

export async function getTestingPolicySummaryData(grouping = "daily", filter) {
  let data = await landingsData;

  // console.log(data);
  // let startDate = grouping == "daily" ? "2020-09-15" : "2020-08-16";
  let startDate = "2020-09-01";
  let dailyData = {};
  for (let date in data) {
    // Ignore data before the testing policy took place.
    if (
      Temporal.PlainDate.compare(
        Temporal.PlainDate.from(date),
        Temporal.PlainDate.from(startDate)
      ) < 1
    ) {
      continue;
    }

    let returnedDataForDate = getNewTestingTagCountObject();

    let originalData = data[date];
    for (let bug of originalData) {
      if (filter && !filter(bug)) {
        continue;
      }
      for (let commit of bug.commits) {
        if (!commit.testing ) {
          returnedDataForDate.unknown++;
        } else {
          returnedDataForDate[commit.testing] =
            returnedDataForDate[commit.testing] + 1;
        }
      }
    }

    dailyData[date] = returnedDataForDate;
  }

  console.log(dailyData);

  if (grouping == "weekly") {
    let weeklyData = {};
    for (let daily in dailyData) {
      let date = Temporal.PlainDate.from(daily);
      let weekStart = date.subtract({ days: date.dayOfWeek }).toString();

      if (!weeklyData[weekStart]) {
        weeklyData[weekStart] = getNewTestingTagCountObject();
      }

      for (let tag in dailyData[daily]) {
        weeklyData[weekStart][tag] += dailyData[daily][tag];
      }
    }

    return weeklyData;
  } else if (grouping == "monthly") {
    let monthlyData = {};
    for (let daily in dailyData) {
      let date = Temporal.PlainDate.from(daily);
      let yearMonth = date.toYearMonth();

      if (!monthlyData[yearMonth]) {
        monthlyData[yearMonth] = getNewTestingTagCountObject();
      }

      for (let tag in dailyData[daily]) {
        monthlyData[yearMonth][tag] += dailyData[daily][tag];
      }
    }
    return monthlyData;
  }

  return dailyData;
}
