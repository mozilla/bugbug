// REFERENCE — use this structure, but replace this comment.
//
// This script checks whether one browser behaves as expected. Implement `probe`
// to perform the test and return the relevant observations, and `isWorking` to
// decide whether the expected behaviour occurred. Keep the rest of the script
// unchanged.
//
// In your script, include a comment describing the reported bug, the expected
// behaviour, and how Firefox differs. Write `isWorking` to verify the expected
// behaviour itself, not just the absence of the reported symptom.
//
// Run the script once per browser:
//   BROWSER=firefox BROWSER_BIN=/path/to/firefox node this-script.mjs
//   BROWSER=chrome  BROWSER_BIN=/path/to/chrome  node this-script.mjs
//
// Exit codes:
//   0 = worked as expected
//   1 = did not work as expected
//   2 = no verdict because the browser or script failed

import puppeteer from "puppeteer";

const { BROWSER, BROWSER_BIN } = process.env;
if (BROWSER !== "firefox" && BROWSER !== "chrome") {
  console.error("set BROWSER to firefox or chrome");
  process.exit(2);
}
if (!BROWSER_BIN) {
  console.error("set BROWSER_BIN to the browser binary");
  process.exit(2);
}

const TARGET = "https://example.com/";

async function probe() {
  const browser = await puppeteer.launch({
    browser: BROWSER,
    executablePath: BROWSER_BIN,
    headless: true,
    ...(BROWSER === "chrome" ? { args: ["--no-sandbox"] } : {}),
  });
  try {
    const page = await browser.newPage();
    await page.goto(TARGET, { waitUntil: "networkidle0" });
    return await page.evaluate(() => ({}));
  } catch (error) {
    return { error: String(error?.message ?? error) };
  } finally {
    await browser.close();
  }
}

function isWorking(state) {
  return false;
}

const RUNS = 3;

let workingRuns = 0;
try {
  for (let i = 1; i <= RUNS; i++) {
    const state = await probe();
    const working = isWorking(state);
    if (working) workingRuns++;
    console.log(`Run ${i}: ${JSON.stringify(state)} working=${working}`);
  }
} catch (error) {
  console.error("FATAL:", error);
  process.exit(2);
}

console.log(`\n${BROWSER}: worked in ${workingRuns}/${RUNS} runs.`);

process.exit(workingRuns === RUNS ? 0 : 1);
