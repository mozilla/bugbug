// REFERENCE — write your own script in this shape; don't copy this comment.
//
// Fill in `probe` (run the steps, return measurements) and `reproduced` (assert
// broken in Firefox AND working in Chrome). Keep the rest as-is. Add a comment in
// your script covering the bug data, the expected behaviour, and what breaks in Firefox.
//
// Run:
//   FIREFOX_BIN=/path/to/firefox CHROME_BIN=/path/to/chrome node this-script.mjs
//
// Exit code:
// 0 = Firefox-specific breakage reproduced: broken in Firefox, working in Chrome.
// 1 = no Firefox-specific breakage: it worked in both, broke in both, or only Chrome broke.
// 2 = no verdict — a browser wouldn't launch, or the script itself broke.

import puppeteer from "puppeteer";

const { FIREFOX_BIN, CHROME_BIN } = process.env;
if (!FIREFOX_BIN || !CHROME_BIN) {
  console.error("set FIREFOX_BIN and CHROME_BIN to the browser binaries");
  process.exit(2);
}

const TARGET = "https://example.com/";

async function probe(name, executablePath) {
  const browser = await puppeteer.launch({
    browser: name,
    executablePath,
    headless: true,
    ...(name === "chrome" ? { args: ["--no-sandbox"] } : {}),
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

function reproduced(ff, cr) {
  return false;
}

const RUNS = 3;

let reproductions = 0;
try {
  for (let i = 1; i <= RUNS; i++) {
    const ff = await probe("firefox", FIREFOX_BIN);
    const cr = await probe("chrome", CHROME_BIN);
    const ok = reproduced(ff, cr);
    if (ok) reproductions++;
    console.log(
      `Run ${i}: firefox=${JSON.stringify(ff)} chrome=${JSON.stringify(
        cr
      )} reproduced=${ok}`
    );
  }
} catch (error) {
  console.error("FATAL:", error);
  process.exit(2);
}

console.log(`\nReproduced ${reproductions}/${RUNS} runs.`);
process.exit(reproductions === RUNS ? 0 : 1);
