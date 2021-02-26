import * as common from "./common";

async function rerender(connections) {
  common.renderDependencyHeatmap(document.querySelector("#chart"));

  document.querySelector("#component-source").textContent = JSON.stringify(
    connections,
    null,
    2
  );
}

(async function () {
  let connectionsMap = await common.getComponentRegressionMap();
  console.log(connectionsMap);
  await rerender(connectionsMap);
})();
