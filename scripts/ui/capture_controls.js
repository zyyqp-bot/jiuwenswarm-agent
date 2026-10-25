// Exercise the controls: stop a running run, open the history drawer, start a
// new task. Captures each state for review. Requires the local server on :8010.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.join(ROOT, ".ui_preview");
const BASE = "http://127.0.0.1:8010";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const GOAL = "学校引入 AI 作业批改工具前，需要完成哪些合规动作？请给出依据条文。";

function judgePassword() {
  const text = fs.readFileSync(path.join(ROOT, "deploy", ".env.production.local"), "utf8");
  const line = text.split(/\r?\n/).find((item) => item.startsWith("WORKBENCH_USERS="));
  const entry = line.slice(line.indexOf("=") + 1).split(";").find((item) => item.startsWith("judge1:"));
  return entry.slice("judge1:".length);
}

(async () => {
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click(".enter-button");
  await page.fill('input[autocomplete="username"]', "judge1");
  await page.fill('input[type="password"]', judgePassword());
  await page.click('button[type="submit"]');
  await page.waitForSelector(".composer");

  await page.fill("textarea", GOAL);
  await page.click('button[type="submit"]');
  await page.waitForSelector(".agent-card");
  await page.waitForTimeout(8000);

  // 1) stop
  await page.click('button:has-text("停止")');
  await page.waitForTimeout(2500);
  const badge = await page.locator(".agent-head .badge").textContent();
  await page.screenshot({ path: path.join(OUT, "control-stopped.png") });

  // 2) history drawer
  await page.click('button:has-text("历史")');
  await page.waitForSelector(".history-drawer");
  await page.waitForTimeout(400);
  await page.locator(".history-drawer").screenshot({ path: path.join(OUT, "control-history.png") });

  // 3) new task
  await page.click(".new-run");
  await page.waitForSelector(".chat-hero");
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, "control-new-task.png") });

  console.log(JSON.stringify({ stoppedBadge: badge, rows: await page.locator(".run-item").count() }));
  await browser.close();
})().catch((error) => {
  console.error("CONTROL_CAPTURE_FAILED", error.message);
  process.exit(1);
});
