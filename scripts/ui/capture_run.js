// Start one real run against the local server and capture the UI stages used
// in the deck: form, task graph + evidence, report + conflicts.
//
// Run: node scripts/ui/capture_run.js   (server on :8010, model credentials set)
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.join(ROOT, ".ui_preview");
const BASE = process.env.UI_BASE || "http://127.0.0.1:8010";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const GOAL =
  "学校拟引入一款 AI 作业批改工具，请依据教育政策与 AI 教育指南，评估其合规要求并给出依据条文。";

function judgePassword() {
  const text = fs.readFileSync(path.join(ROOT, "deploy", ".env.production.local"), "utf8");
  const line = text.split(/\r?\n/).find((item) => item.startsWith("WORKBENCH_USERS="));
  const value = line.slice(line.indexOf("=") + 1);
  const entry = value.split(";").find((item) => item.startsWith("judge1:"));
  return entry.slice("judge1:".length);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.fill('input[autocomplete="username"]', "judge1");
  await page.fill('input[type="password"]', judgePassword());
  await page.click('button[type="submit"]');
  await page.waitForSelector(".panel.controls");

  await page.selectOption("select", "education-policy");
  await page.fill("textarea", GOAL);
  await page.locator(".panel.controls").screenshot({ path: path.join(OUT, "new-task.png") });

  await page.click('button[type="submit"]');
  await page.waitForSelector(".task-graph", { timeout: 120000 });
  await page.waitForTimeout(25000);
  await page.locator(".panes").screenshot({ path: path.join(OUT, "task-graph.png") });

  // Terminal: the report pane appears only after the run finishes.
  await page.waitForSelector(".report-pane pre", { timeout: 420000 });
  await page.waitForTimeout(1500);
  const report = page.locator(".conflicts-pane");
  await report.screenshot({ path: path.join(OUT, "report-conflicts.png") });
  const reportBody = page.locator(".report-pane");
  await reportBody.screenshot({ path: path.join(OUT, "report-pane.png") });
  await page.screenshot({ path: path.join(OUT, "workbench-done.png") });

  const state = await page.evaluate(() => {
    const badge = document.querySelector(".badge");
    return { phase: badge ? badge.textContent : null };
  });
  console.log(JSON.stringify(state));
  await browser.close();
})().catch((error) => {
  console.error("CAPTURE_FAILED", error.message);
  process.exit(1);
});
