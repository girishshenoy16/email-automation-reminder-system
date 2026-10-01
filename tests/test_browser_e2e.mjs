/**
 * End-to-end Browser Verification for Email Automation & Reminder System Dashboard.
 * Connects directly to Google Chrome via Chrome DevTools Protocol (CDP).
 *
 * Verifies:
 * 1. Zero JavaScript console errors on load.
 * 2. Header badges, timestamps (Data Snapshot vs Page Rendered).
 * 3. 6 KPI cards with reconciled values (25, 18, 4, 12, 100.0%, 1).
 * 4. Chart.js rendering for all 3 charts (Donut, Department, Timeline).
 * 5. Full-width timeline canvas dimensions (no clipping or right-side blank space).
 * 6. Audit table renders all 18 reconciled records.
 * 7. Slicer filtering (Department = Finance -> 6 records, recomputed KPIs & charts).
 * 8. Reset Filters functionality (restores 18 records).
 * 9. Search box filtering (e.g. "Webinar" -> 4 records).
 * 10. Interactive simulation mode (adds SIMULATED record, preserves SMTP rate at 100.0%).
 * 11. Email template preview switching (Meeting -> Payment -> Webinar).
 */

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawn } from "node:child_process";

const HTTP_PORT = 8765;
const CDP_PORT = 9876;
const DOCS_DIR = path.resolve("docs");
const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const tempUserDataDir = path.join(os.tmpdir(), "chrome-cdp-" + Date.now());

// 1. Simple static file server
const server = http.createServer((req, res) => {
  let reqPath = req.url.split("?")[0];
  if (reqPath === "/") reqPath = "/index.html";
  const filePath = path.join(DOCS_DIR, reqPath);

  if (!fs.existsSync(filePath)) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    return res.end("Not Found");
  }

  const ext = path.extname(filePath);
  const mimeTypes = {
    ".html": "text/html",
    ".css":  "text/css",
    ".js":   "application/javascript",
    ".json": "application/json",
  };

  res.writeHead(200, { "Content-Type": mimeTypes[ext] || "application/octet-stream" });
  fs.createReadStream(filePath).pipe(res);
});

await new Promise(resolve => server.listen(HTTP_PORT, "127.0.0.1", resolve));
console.log(`[TEST-SERVER] Serving docs/ at http://127.0.0.1:${HTTP_PORT}`);

// 2. Launch headless Chrome with remote debugging on CDP_PORT
const chrome = spawn(CHROME_PATH, [
  "--headless=new",
  `--remote-debugging-port=${CDP_PORT}`,
  `--user-data-dir=${tempUserDataDir}`,
  "--disable-gpu",
  "--no-sandbox",
  `http://127.0.0.1:${HTTP_PORT}/index.html`,
], { stdio: "ignore" });

// Wait for Chrome CDP port to open
let pageWsUrl = null;
for (let i = 0; i < 20; i++) {
  try {
    const listRes = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
    const pages = await listRes.json();
    const target = pages.find(p => p.url.includes(String(HTTP_PORT)));
    if (target && target.webSocketDebuggerUrl) {
      pageWsUrl = target.webSocketDebuggerUrl;
      break;
    }
  } catch (e) {
    // wait and retry
  }
  await new Promise(r => setTimeout(r, 250));
}

if (!pageWsUrl) {
  console.error("FAILED to connect to Chrome CDP");
  chrome.kill();
  server.close();
  process.exit(1);
}

console.log("[CDP] Connected to Chrome debugging target:", pageWsUrl);

// 3. Connect via WebSocket
const ws = new WebSocket(pageWsUrl);
await new Promise(resolve => ws.onopen = resolve);

let idCounter = 1;
const pending = new Map();
const consoleErrors = [];

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
  if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
    consoleErrors.push(msg.params.args.map(a => a.value || a.description).join(" "));
  }
};

function send(method, params = {}) {
  const id = idCounter++;
  return new Promise(resolve => {
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const res = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (res.result?.exceptionDetails) {
    throw new Error(res.result.exceptionDetails.exception.description);
  }
  return res.result?.result?.value;
}

await send("Runtime.enable");
await send("Page.enable");

// Wait 1.5 seconds for Chart.js CDN, data fetch, and initial rendering
await new Promise(r => setTimeout(r, 1500));

console.log("\n==================== BROWSER E2E VERIFICATION ====================");

// TEST 1: Zero console errors
if (consoleErrors.length > 0) {
  console.error("FAIL: JavaScript console errors detected:", consoleErrors);
  chrome.kill();
  server.close();
  process.exit(1);
}
console.log("PASS 1: Zero JavaScript console errors on load.");

// TEST 2: Status badge and timestamps
const statusBadge = await evaluate("document.getElementById('statusBadge').textContent.trim()");
const headerTs = await evaluate("document.getElementById('headerTimestamp').textContent.trim()");
const renderTs = await evaluate("document.getElementById('renderTimestamp').textContent.trim()");

console.log(`Status Badge: "${statusBadge}"`);
console.log(`Header Timestamp: "${headerTs}"`);
console.log(`Render Timestamp: "${renderTs}"`);

if (!statusBadge.includes("DEMO MODE")) throw new Error("Status badge must contain 'DEMO MODE'");
if (!headerTs.includes("Data Snapshot")) throw new Error("Header timestamp must show Data Snapshot");
if (!renderTs.includes("Page Loaded")) throw new Error("Render timestamp must show Page Loaded");
console.log("PASS 2: Status badge is 'DEMO MODE' and timestamps clearly separate snapshot from render time.");

// TEST 3: Reconciled 6 KPI Cards
const kpis = await evaluate(`({
  contacts: document.getElementById('kpiContacts').textContent.trim(),
  reminders: document.getElementById('kpiReminders').textContent.trim(),
  smtpAccepted: document.getElementById('kpiSmtpAccepted').textContent.trim(),
  simulated: document.getElementById('kpiSimulated').textContent.trim(),
  rate: document.getElementById('kpiRate').textContent.trim(),
  review: document.getElementById('kpiReview').textContent.trim(),
})`);

console.log("Initial KPI Values:", kpis);
if (kpis.contacts !== "25") throw new Error(`Expected Total Contacts 25, got ${kpis.contacts}`);
if (kpis.reminders !== "18") throw new Error(`Expected Active Reminders 18, got ${kpis.reminders}`);
if (kpis.smtpAccepted !== "4") throw new Error(`Expected SMTP Accepted 4, got ${kpis.smtpAccepted}`);
if (kpis.simulated !== "12") throw new Error(`Expected Simulated 12, got ${kpis.simulated}`);
if (kpis.rate !== "100.0%") throw new Error(`Expected SMTP Acceptance Rate 100.0%, got ${kpis.rate}`);
if (kpis.review !== "1") throw new Error(`Expected Requires Review 1, got ${kpis.review}`);
console.log("PASS 3: All 6 KPI cards display accurate, reconciled values on initial load.");

// TEST 4: Chart.js Canvases & Width Sizing (Timeline full-width check)
const chartInfo = await evaluate(`({
  donutExists: !!Chart.getChart('chartDonut'),
  deptExists: !!Chart.getChart('chartDepartment'),
  timelineExists: !!Chart.getChart('chartTimeline'),
  timelineCanvasWidth: document.getElementById('chartTimeline').clientWidth,
  timelineParentWidth: document.querySelector('.chart-card-wide').clientWidth,
})`);

console.log("Chart rendering:", chartInfo);
if (!chartInfo.donutExists || !chartInfo.deptExists || !chartInfo.timelineExists) {
  throw new Error("One or more Chart.js charts failed to initialize.");
}
const widthRatio = chartInfo.timelineCanvasWidth / chartInfo.timelineParentWidth;
console.log(`Timeline canvas width ratio to card: ${(widthRatio * 100).toFixed(1)}%`);
if (widthRatio < 0.85) {
  throw new Error(`Timeline chart does not fill wide container width (ratio: ${widthRatio})`);
}
console.log("PASS 4: All 3 charts rendered; Timeline chart spans full panel width without blank space.");

// TEST 5: Audit table row count & recipient masking
const initialRowCount = await evaluate("document.querySelectorAll('#auditTableBody tr').length");
const recordBadge = await evaluate("document.getElementById('recordCount').textContent.trim()");
console.log(`Audit Table rows: ${initialRowCount} | Badge: "${recordBadge}"`);
if (initialRowCount !== 18) throw new Error(`Expected 18 audit table rows, got ${initialRowCount}`);
if (!recordBadge.includes("18")) throw new Error(`Expected badge to mention 18 records, got ${recordBadge}`);

const recipientEmails = await evaluate("Array.from(document.querySelectorAll('.col-recipient')).map(td => td.textContent.trim())");
if (recipientEmails.length !== 18) throw new Error("Expected 18 recipient cells");
const unmasked = recipientEmails.filter(e => !e.includes("***"));
if (unmasked.length > 0) throw new Error(`Found unmasked recipient emails: ${unmasked.join(", ")}`);
console.log("PASS 5: Audit table displays all 18 reconciled synthetic records with masked recipient emails (e.g. a***@example.com).");

// TEST 6: Slicer Filtering (Department = Finance)
await evaluate(`
  document.getElementById('slicerDepartment').value = 'Finance';
  document.getElementById('slicerDepartment').dispatchEvent(new Event('change'));
`);
await new Promise(r => setTimeout(r, 200));

const financeView = await evaluate(`({
  rows: document.querySelectorAll('#auditTableBody tr').length,
  badge: document.getElementById('recordCount').textContent.trim(),
  smtpAccepted: document.getElementById('kpiSmtpAccepted').textContent.trim(),
  simulated: document.getElementById('kpiSimulated').textContent.trim(),
  rate: document.getElementById('kpiRate').textContent.trim(),
})`);

console.log("Finance Filter View:", financeView);
if (financeView.rows !== 6) throw new Error(`Expected 6 Finance rows, got ${financeView.rows}`);
if (financeView.smtpAccepted !== "3") throw new Error(`Expected 3 SMTP Accepted in Finance, got ${financeView.smtpAccepted}`);
if (financeView.simulated !== "2") throw new Error(`Expected 2 Simulated in Finance, got ${financeView.simulated}`);
if (financeView.rate !== "100.0%") throw new Error(`Expected 100.0% SMTP rate in Finance, got ${financeView.rate}`);
console.log("PASS 6: Department filter updates audit table, KPIs, and charts responsively.");

// TEST 7: Reset Filters
await evaluate("document.getElementById('btnResetFilters').click()");
await new Promise(r => setTimeout(r, 200));

const resetRowCount = await evaluate("document.querySelectorAll('#auditTableBody tr').length");
const deptVal = await evaluate("document.getElementById('slicerDepartment').value");
if (resetRowCount !== 18 || deptVal !== "all") {
  throw new Error("Reset Filters failed to restore all 18 records.");
}
console.log("PASS 7: Reset Filters button restores full 18-record dataset and resets all slicers.");

// TEST 8: Text Search Box (e.g. "webinar")
await evaluate(`
  const s = document.getElementById('tableSearch');
  s.value = 'webinar';
  s.dispatchEvent(new Event('input'));
`);
await new Promise(r => setTimeout(r, 200));

const searchRows = await evaluate("document.querySelectorAll('#auditTableBody tr').length");
console.log(`Search 'webinar' returned rows: ${searchRows}`);
if (searchRows !== 4) throw new Error(`Expected 4 Webinar records, got ${searchRows}`);
console.log("PASS 8: Audit log text search filters records interactively.");

// Clear search
await evaluate(`{
  const s = document.getElementById('tableSearch');
  s.value = '';
  s.dispatchEvent(new Event('input'));
}`);
await new Promise(r => setTimeout(r, 100));

// TEST 9: Interactive Browser Simulation Mode
await evaluate("document.getElementById('btnSimulate').click()");
await new Promise(r => setTimeout(r, 800)); // wait for simulation delay

const simResult = await evaluate(`({
  rows: document.querySelectorAll('#auditTableBody tr').length,
  simulatedKpi: document.getElementById('kpiSimulated').textContent.trim(),
  contactsKpi: document.getElementById('kpiContacts').textContent.trim(),
  remindersKpi: document.getElementById('kpiReminders').textContent.trim(),
  smtpKpi: document.getElementById('kpiSmtpAccepted').textContent.trim(),
  rateKpi: document.getElementById('kpiRate').textContent.trim(),
  firstRowId: document.querySelector('#auditTableBody tr td').textContent.trim(),
  firstRowRecipient: document.querySelector('#auditTableBody tr .col-recipient').textContent.trim(),
})`);

console.log("Simulation Result:", simResult);
if (simResult.rows !== 19) throw new Error(`Expected 19 rows after simulation, got ${simResult.rows}`);
if (simResult.simulatedKpi !== "13") throw new Error(`Expected 13 Simulated dispatches, got ${simResult.simulatedKpi}`);
if (simResult.contactsKpi !== "25") throw new Error("Total contacts should remain 25");
if (simResult.remindersKpi !== "18") throw new Error("Active reminders should remain 18");
if (simResult.smtpKpi !== "4") throw new Error("SMTP accepted must NOT increase on simulation");
if (simResult.rateKpi !== "100.0%") throw new Error("SMTP Acceptance Rate must NOT change on simulation");
if (!simResult.firstRowId.startsWith("SIM-")) throw new Error("First row should be newly simulated record");
if (simResult.firstRowRecipient !== "d***@example.com") throw new Error(`Expected masked simulated recipient 'd***@example.com', got '${simResult.firstRowRecipient}'`);
console.log("PASS 9: Simulation operates in client-side memory: increments Simulated count, preserves structural KPIs & SMTP acceptance rate, masks simulated recipient.");

// TEST 10: Email Template Preview
const meetingContent = await evaluate("document.getElementById('templatePreviewFrame').textContent");
if (!meetingContent.includes("Meeting Alert")) throw new Error("Meeting template should be rendered by default");

// Switch to Payment
await evaluate(`
  document.getElementById('templateSelector').value = 'payment';
  document.getElementById('templateSelector').dispatchEvent(new Event('change'));
`);
const paymentContent = await evaluate("document.getElementById('templatePreviewFrame').textContent");
if (!paymentContent.includes("Payment & Invoice Alert")) throw new Error("Payment template failed to render on change");

// Switch to Webinar
await evaluate(`
  document.getElementById('templateSelector').value = 'webinar';
  document.getElementById('btnPreview').click();
`);
const webinarContent = await evaluate("document.getElementById('templatePreviewFrame').textContent");
if (!webinarContent.includes("Webinar Alert")) throw new Error("Webinar template failed to render on preview button click");
console.log("PASS 10: Template preview auto-renders on load, and updates seamlessly on select change and button click.");

// TEST 11: Three-Tab Architecture & Panel Switching (Overview Default)
const tabStateInitial = await evaluate(`({
  headerHasTabs: document.querySelector('.header-inner .tabs-nav') !== null,
  bodyHasTabsNav: document.querySelector('.tabs-container .tabs-nav') !== null,
  overviewBtnActive: document.getElementById('tabOverviewBtn').classList.contains('active'),
  overviewBtnAria: document.getElementById('tabOverviewBtn').getAttribute('aria-selected'),
  overviewPanelVisible: window.getComputedStyle(document.getElementById('tabOverviewPanel')).display !== 'none',
  auditBtnActive: document.getElementById('tabAuditBtn').classList.contains('active'),
  auditBtnAria: document.getElementById('tabAuditBtn').getAttribute('aria-selected'),
  auditPanelVisible: window.getComputedStyle(document.getElementById('tabAuditPanel')).display !== 'none',
  templateBtnActive: document.getElementById('tabTemplateBtn').classList.contains('active'),
  templateBtnAria: document.getElementById('tabTemplateBtn').getAttribute('aria-selected'),
  templatePanelVisible: window.getComputedStyle(document.getElementById('tabTemplatePanel')).display !== 'none',
})`);

if (!tabStateInitial.headerHasTabs) {
  throw new Error("Tab navigation .tabs-nav must be located inside .header-inner in the blue header");
}
if (tabStateInitial.bodyHasTabsNav) {
  throw new Error("Old tab navigation bar below timeline must be removed (duplicate found)");
}
if (!tabStateInitial.overviewBtnActive || tabStateInitial.overviewBtnAria !== 'true' || !tabStateInitial.overviewPanelVisible) {
  throw new Error("Initial state: Tab 1 (Overview) should be active and visible by default");
}
if (tabStateInitial.auditBtnActive || tabStateInitial.auditBtnAria !== 'false' || tabStateInitial.auditPanelVisible) {
  throw new Error("Initial state: Tab 2 (Recent Dispatch Audit Log) should be inactive and hidden");
}
if (tabStateInitial.templateBtnActive || tabStateInitial.templateBtnAria !== 'false' || tabStateInitial.templatePanelVisible) {
  throw new Error("Initial state: Tab 3 (Email Template Preview) should be inactive and hidden");
}

// Click Tab 2: Recent Dispatch Audit Log
await evaluate("document.getElementById('tabAuditBtn').click()");
await new Promise(r => setTimeout(r, 100));

const tabStateAudit = await evaluate(`({
  overviewPanelVisible: window.getComputedStyle(document.getElementById('tabOverviewPanel')).display !== 'none',
  auditBtnActive: document.getElementById('tabAuditBtn').classList.contains('active'),
  auditBtnAria: document.getElementById('tabAuditBtn').getAttribute('aria-selected'),
  auditPanelVisible: window.getComputedStyle(document.getElementById('tabAuditPanel')).display !== 'none',
  templatePanelVisible: window.getComputedStyle(document.getElementById('tabTemplatePanel')).display !== 'none',
  auditRowCount: document.querySelectorAll('#auditTableBody tr').length,
})`);

if (!tabStateAudit.auditBtnActive || tabStateAudit.auditBtnAria !== 'true' || !tabStateAudit.auditPanelVisible) {
  throw new Error("Tab 2 (Audit Log) failed to activate and display panel");
}
if (tabStateAudit.overviewPanelVisible || tabStateAudit.templatePanelVisible) {
  throw new Error("When Tab 2 is active, Overview and Template panels must be hidden");
}
if (tabStateAudit.auditRowCount < 18) {
  throw new Error(`Audit log rows missing in Tab 2: got ${tabStateAudit.auditRowCount}`);
}

// Click Tab 3: Email Template Preview
await evaluate("document.getElementById('tabTemplateBtn').click()");
await new Promise(r => setTimeout(r, 100));

const tabStateTemplate = await evaluate(`({
  overviewPanelVisible: window.getComputedStyle(document.getElementById('tabOverviewPanel')).display !== 'none',
  auditPanelVisible: window.getComputedStyle(document.getElementById('tabAuditPanel')).display !== 'none',
  templateBtnActive: document.getElementById('tabTemplateBtn').classList.contains('active'),
  templateBtnAria: document.getElementById('tabTemplateBtn').getAttribute('aria-selected'),
  templatePanelVisible: window.getComputedStyle(document.getElementById('tabTemplatePanel')).display !== 'none',
  previewHasContent: document.getElementById('templatePreviewFrame').textContent.length > 20,
})`);

if (!tabStateTemplate.templateBtnActive || tabStateTemplate.templateBtnAria !== 'true' || !tabStateTemplate.templatePanelVisible) {
  throw new Error("Tab 3 (Template Preview) failed to activate and display panel");
}
if (tabStateTemplate.overviewPanelVisible || tabStateTemplate.auditPanelVisible) {
  throw new Error("When Tab 3 is active, Overview and Audit panels must be hidden");
}
if (!tabStateTemplate.previewHasContent) {
  throw new Error("Template preview frame is empty when Tab 3 is active");
}

// Click Tab 1: Overview to switch back and verify charts render/resize properly
await evaluate("document.getElementById('tabOverviewBtn').click()");
await new Promise(r => setTimeout(r, 150));

const tabStateRestored = await evaluate(`({
  overviewBtnActive: document.getElementById('tabOverviewBtn').classList.contains('active'),
  overviewPanelVisible: window.getComputedStyle(document.getElementById('tabOverviewPanel')).display !== 'none',
  auditPanelVisible: window.getComputedStyle(document.getElementById('tabAuditPanel')).display !== 'none',
  templatePanelVisible: window.getComputedStyle(document.getElementById('tabTemplatePanel')).display !== 'none',
  donutWidth: document.getElementById('chartDonut').clientWidth,
  donutHeight: document.getElementById('chartDonut').clientHeight,
  timelineWidth: document.getElementById('chartTimeline').clientWidth,
  parentWidth: document.querySelector('.chart-card-wide').clientWidth,
})`);

if (!tabStateRestored.overviewBtnActive || !tabStateRestored.overviewPanelVisible) {
  throw new Error("Tab 1 (Overview) failed to restore active state upon clicking Overview button");
}
if (tabStateRestored.auditPanelVisible || tabStateRestored.templatePanelVisible) {
  throw new Error("When Tab 1 is active, Audit and Template panels must be hidden");
}
if (tabStateRestored.donutWidth === 0 || tabStateRestored.donutHeight === 0 || tabStateRestored.timelineWidth === 0) {
  throw new Error("Chart canvases collapsed to 0 dimensions when switching back to Overview tab");
}
const restoredWidthRatio = tabStateRestored.timelineWidth / tabStateRestored.parentWidth;
if (restoredWidthRatio < 0.85) {
  throw new Error(`Timeline chart canvas does not span wide container on tab switch (ratio: ${restoredWidthRatio})`);
}

// Sub-check: Off-screen slicer change test (switch to Tab 2, change filter, switch back to Tab 1, verify chart resizing)
await evaluate("document.getElementById('tabAuditBtn').click()");
await new Promise(r => setTimeout(r, 100));

await evaluate(`
  document.getElementById('slicerDepartment').value = 'Engineering';
  document.getElementById('slicerDepartment').dispatchEvent(new Event('change'));
`);
await new Promise(r => setTimeout(r, 100));

await evaluate("document.getElementById('tabOverviewBtn').click()");
await new Promise(r => setTimeout(r, 150));

const offscreenChartState = await evaluate(`({
  donutWidth: document.getElementById('chartDonut').clientWidth,
  donutHeight: document.getElementById('chartDonut').clientHeight,
  timelineWidth: document.getElementById('chartTimeline').clientWidth,
  parentWidth: document.querySelector('.chart-card-wide').clientWidth,
})`);

if (offscreenChartState.donutWidth === 0 || offscreenChartState.timelineWidth === 0) {
  throw new Error("Charts failed to resize after offscreen filter change");
}
if ((offscreenChartState.timelineWidth / offscreenChartState.parentWidth) < 0.85) {
  throw new Error("Timeline chart squashed after offscreen filter change");
}

// Reset filters to restore clean state
await evaluate("document.getElementById('btnResetFilters').click()");
await new Promise(r => setTimeout(r, 100));

console.log("PASS 11: Three-tab navigation works seamlessly: Overview active by default, all 3 panels switch cleanly, charts resize without distortion even across offscreen slicer modifications.");

// TEST 12: Keyboard Accessibility Across All 3 Tabs (ArrowRight, ArrowLeft, Home, End)
// Start focused on Overview tab
await evaluate(`
  const overviewBtn = document.getElementById('tabOverviewBtn');
  overviewBtn.focus();
  // ArrowRight -> should activate Audit Log (Tab 2)
  overviewBtn.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
`);
await new Promise(r => setTimeout(r, 100));

const keyStep1 = await evaluate(`({
  activeId: document.activeElement.id,
  auditAria: document.getElementById('tabAuditBtn').getAttribute('aria-selected'),
  auditVisible: window.getComputedStyle(document.getElementById('tabAuditPanel')).display !== 'none',
})`);
if (keyStep1.activeId !== 'tabAuditBtn' || keyStep1.auditAria !== 'true' || !keyStep1.auditVisible) {
  throw new Error(`ArrowRight from Overview failed: active=${keyStep1.activeId}`);
}

// ArrowRight -> should activate Template Preview (Tab 3)
await evaluate(`
  document.getElementById('tabAuditBtn').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
`);
await new Promise(r => setTimeout(r, 100));

const keyStep2 = await evaluate(`({
  activeId: document.activeElement.id,
  templateAria: document.getElementById('tabTemplateBtn').getAttribute('aria-selected'),
  templateVisible: window.getComputedStyle(document.getElementById('tabTemplatePanel')).display !== 'none',
})`);
if (keyStep2.activeId !== 'tabTemplateBtn' || keyStep2.templateAria !== 'true' || !keyStep2.templateVisible) {
  throw new Error(`ArrowRight from Audit Log failed: active=${keyStep2.activeId}`);
}

// ArrowRight -> should loop back to Overview (Tab 1)
await evaluate(`
  document.getElementById('tabTemplateBtn').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
`);
await new Promise(r => setTimeout(r, 100));

const keyStep3 = await evaluate(`({
  activeId: document.activeElement.id,
  overviewAria: document.getElementById('tabOverviewBtn').getAttribute('aria-selected'),
  overviewVisible: window.getComputedStyle(document.getElementById('tabOverviewPanel')).display !== 'none',
})`);
if (keyStep3.activeId !== 'tabOverviewBtn' || keyStep3.overviewAria !== 'true' || !keyStep3.overviewVisible) {
  throw new Error(`ArrowRight loop back to Overview failed: active=${keyStep3.activeId}`);
}

// ArrowLeft -> should loop back to Template Preview (Tab 3)
await evaluate(`
  document.getElementById('tabOverviewBtn').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
`);
await new Promise(r => setTimeout(r, 100));

const keyStep4 = await evaluate(`({
  activeId: document.activeElement.id,
  templateAria: document.getElementById('tabTemplateBtn').getAttribute('aria-selected'),
  templateVisible: window.getComputedStyle(document.getElementById('tabTemplatePanel')).display !== 'none',
})`);
if (keyStep4.activeId !== 'tabTemplateBtn' || keyStep4.templateAria !== 'true' || !keyStep4.templateVisible) {
  throw new Error(`ArrowLeft loop from Overview to Template failed: active=${keyStep4.activeId}`);
}

// Home key -> should jump to Overview (Tab 1)
await evaluate(`
  document.getElementById('tabTemplateBtn').dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
`);
await new Promise(r => setTimeout(r, 100));

const keyStep5 = await evaluate(`({
  activeId: document.activeElement.id,
  overviewAria: document.getElementById('tabOverviewBtn').getAttribute('aria-selected'),
  overviewVisible: window.getComputedStyle(document.getElementById('tabOverviewPanel')).display !== 'none',
})`);
if (keyStep5.activeId !== 'tabOverviewBtn' || keyStep5.overviewAria !== 'true' || !keyStep5.overviewVisible) {
  throw new Error(`Home key navigation failed: active=${keyStep5.activeId}`);
}

console.log("PASS 12: Tab navigation is fully accessible via keyboard across all 3 tabs (ArrowRight, ArrowLeft, Home, looping & focus management).");

// TEST 13: Global Horizontal Center Alignment & Responsive Viewport Check (Desktop, Tablet, Mobile)
const viewports = [
  { name: "Desktop", width: 1440, height: 900 },
  { name: "Tablet",  width: 768,  height: 1024 },
  { name: "Mobile",  width: 375,  height: 667 },
];

for (const vp of viewports) {
  await send("Emulation.setDeviceMetricsOverride", {
    width: vp.width,
    height: vp.height,
    deviceScaleFactor: 1,
    mobile: vp.width < 768,
  });
  await new Promise(r => setTimeout(r, 150));

  const vpMetrics = await evaluate(`({
    viewportWidth: window.innerWidth,
    docScrollWidth: document.documentElement.scrollWidth,
    docClientWidth: document.documentElement.clientWidth,
    kpiCardAlign: window.getComputedStyle(document.querySelector('.kpi-card')).textAlign,
    tabsNavJustify: window.getComputedStyle(document.querySelector('.header-inner .tabs-nav')).justifyContent,
  })`);

  console.log(`[RESPONSIVE] ${vp.name} (${vp.width}x${vp.height}):`, vpMetrics);
  if (vpMetrics.docScrollWidth > vpMetrics.docClientWidth + 1) {
    throw new Error(`[${vp.name}] Horizontal overflow detected: scrollWidth=${vpMetrics.docScrollWidth}, clientWidth=${vpMetrics.docClientWidth}`);
  }
  if (vpMetrics.kpiCardAlign !== 'center') {
    throw new Error(`[${vp.name}] Expected KPI card text-align: center, got ${vpMetrics.kpiCardAlign}`);
  }
  if (vpMetrics.tabsNavJustify !== 'center') {
    throw new Error(`[${vp.name}] Expected header tabs-nav justify-content: center, got ${vpMetrics.tabsNavJustify}`);
  }
}
console.log("PASS 13: Dashboard is horizontally centered globally with zero horizontal overflow across Desktop, Tablet, and Mobile viewports.");

console.log("\n==================== ALL 13 BROWSER E2E TESTS PASSED ====================\n");

// Cleanup
chrome.kill();
server.close();
process.exit(0);
