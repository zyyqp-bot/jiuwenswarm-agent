// Capture the login page and the workbench from a local server with Edge.
// The judge password is read from the git-ignored env file and never printed.
//
// Run (server must already be on :8010):
//   node scripts/ui/screenshot.js
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.join(ROOT, ".ui_preview");
const BASE = process.env.UI_BASE || "http://127.0.0.1:8010";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

function judgePassword() {
  const text = fs.readFileSync(path.join(ROOT, "deploy", ".env.production.local"), "utf8");
  const line = text.split(/\r?\n/).find((item) => item.startsWith("WORKBENCH_USERS="));
  if (!line) throw new Error("WORKBENCH_USERS not found");
  const entry = line
    .slice("WORKBENCH_USERS=".length)
    .split(";")
    .find((item) => item.startsWith("judge1:"));
  if (!entry) throw new Error("judge1 not found");
  return entry.slice("judge1:".length);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await page.screenshot({ path: path.join(OUT, "landing.png") });
  await page.click(".enter-button");
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, "login.png") });

  await page.fill('input[autocomplete="username"]', "judge1");
  await page.fill('input[type="password"]', judgePassword());
  await page.click('button[type="submit"]');
  await page.waitForSelector(".composer");
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, "workbench.png") });
  await page.screenshot({ path: path.join(OUT, "workbench-full.png"), fullPage: true });

  const glass = await page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll("[data-glass='on']"));
    return {
      count: nodes.length,
      filters: nodes
        .map((node) => node.style.backdropFilter)
        .filter(Boolean)
        .map((value) => value.slice(0, 40)),
    };
  });
  console.log(JSON.stringify({ glass, errors: errors.slice(0, 5) }));
  await browser.close();
})().catch((error) => {
  console.error("SCREENSHOT_FAILED", error.message);
  process.exit(1);
});
