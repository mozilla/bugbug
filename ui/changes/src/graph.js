import * as common from "./common";

async function rerender(connections) {
  const target_components = Object.keys(
    connections[Object.keys(connections)[0]]
  );
  const source_components = target_components.reduce(
    (acc, target_component) =>
      acc.concat(Object.keys(connections[target_component])),
    []
  );

  common.renderDependencyHeatmap(
    document.querySelector("#chart"),
    source_components,
    target_components
  );

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
