const https = require("https");
const functions = require("@google-cloud/functions-framework");
const Diff2html = require("diff2html");

const agent = new https.Agent({ keepAlive: true });
const headers = new Headers({
  "User-Agent": "bugbug-diff2html",
});
const configuration = {
  // Diff2Html Configuration
  outputFormat: "line-by-line",
  matching: "lines",
  renderNothingWhenEmpty: false,
  diffStyle: "word",
  // Diff2HtmlUI Configuration
  synchronisedScroll: true,
  highlight: true,
  fileListToggle: true,
  fileListStartVisible: false,
  fileContentToggle: true,
  stickyFileHeaders: true,
};

/**
 * Responds to any HTTP request.
 *
 * @param {!express:Request} req HTTP request context.
 * @param {!express:Response} res HTTP response context.
 */
functions.http("diff2html", (req, res) => {
  res.set("Access-Control-Allow-Origin", "*");

  let revision_id = req.query.revision_id;
  let diff_id = req.query.diff_id;
  let changeset = req.query.changeset;
  let enableJS = req.query.format !== "html";

  if (
    changeset == undefined &&
    (revision_id == undefined || diff_id == undefined)
  ) {
    res.status(400).send("Missing required parameters");
    return;
  }

  const url =
    changeset != undefined
      ? `https://hg.mozilla.org/mozilla-central/raw-rev/${changeset}`
      : `https://phabricator.services.mozilla.com/D${revision_id}?id=${diff_id}&download=true`;

  fetch(url, { agent, headers })
    .then((res) => {
      if (!res.ok) throw Error(res.statusText);
      return res.text();
    })
    .then((text) => strDiff2Html(text, enableJS))
    .then((output) => res.status(200).send(output))
    .catch((err) => res.status(500).send(`Error: ${err.message}`));
});

const jsTemplate = `
<meta charset="utf-8" />
<link
  rel="stylesheet"
  href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/10.7.1/styles/github.min.css"
/>
<script
  type="text/javascript"
  src="https://cdn.jsdelivr.net/npm/diff2html/bundles/js/diff2html-ui.min.js"
></script>
<script>
  document.addEventListener("DOMContentLoaded", () => {
    const targetElement = document.getElementById("diff");
    const diff2htmlUi = new Diff2HtmlUI(targetElement);
    diff2htmlUi.fileListToggle(false);
    diff2htmlUi.fileContentToggle();
    diff2htmlUi.synchronisedScroll();
    diff2htmlUi.highlightCode();
  });
</script>
`;

function strDiff2Html(strDiff, enableJS) {
  const diffHtml = Diff2html.html(strDiff, configuration);
  return `<!DOCTYPE html>
<html lang="en-us">
  <head>${enableJS ? jsTemplate : ""}
    <link
      rel="stylesheet"
      type="text/css"
      href="https://cdn.jsdelivr.net/npm/diff2html/bundles/css/diff2html.min.css"
    />
  </head>
  <body>
    <div id="diff">${diffHtml}</div>
  </body>
</html>
`;
}
