/**
 * Email Automation & Reminder System — Dashboard Client Logic
 * Fetches ./data/dashboard_data.json relative to docs/ web root.
 * Renders KPI cards, Chart.js visuals, audit table, template previews,
 * interactive slicers, and a client-side demo simulation mode.
 *
 * Safety & Architecture Guarantees:
 *   - Static GitHub Pages compatible (pure client-side HTML/CSS/JS + Chart.js).
 *   - No SMTP network sockets, no backend server, no operational state mutations.
 *   - Fixture immutability: fixtureData is deeply cloned and never modified.
 *   - Simulation isolation: browser-session simulation lives strictly in memory.
 *   - SIMULATED records are never counted as SMTP_ACCEPTED.
 *   - REQUIRES_REVIEW records are quarantined and never counted as accepted.
 *   - SMTP Acceptance Rate = smtp_accepted / (smtp_accepted + failed live attempts).
 *   - All timestamps and public data use synthetic example.com domains with zero PII.
 */

"use strict";

// === PowerBI Corporate Palette Constants ===
const PBI = {
  blue:  "#118DFF",
  navy:  "#12239E",
  gold:  "#F2C80F",
  green: "#107C41",
  red:   "#D83B01",
  teal:  "#00B4D8",
  muted: "#9CA3AF",
  text:  "#1F2937",
};

// === Status Label and Color Mappings ===
const STATUS_CONFIG = {
  SMTP_ACCEPTED:   { label: "SMTP Accepted",   color: PBI.green, pill: "pill-accepted" },
  SIMULATED:       { label: "Simulated",       color: PBI.blue,  pill: "pill-simulated" },
  REQUIRES_REVIEW: { label: "Requires Review", color: PBI.gold,  pill: "pill-review" },
  PENDING:         { label: "Pending",         color: PBI.muted, pill: "pill-pending" },
  FAILED:          { label: "Failed",          color: PBI.red,   pill: "pill-failed" },
};

// === Module State ===
let fixtureData = null;       // Immutable deep clone of loaded JSON fixture
let sessionSimEntries = [];   // Session-only simulated records (never modifies fixture)
let sessionSimCount = 0;      // Counter for simulated records

// Chart.js instances
let donutChart = null;
let deptChart = null;
let timelineChart = null;

// ============================================================
// BOOTSTRAP & INITIALIZATION
// ============================================================
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const resp = await fetch("./data/dashboard_data.json");
    if (!resp.ok) throw new Error(`HTTP ${resp.status} - Failed to load dashboard data`);
    
    // Deep clone fixture data to guarantee immutability
    const rawJson = await resp.text();
    fixtureData = JSON.parse(rawJson);

    // Set timestamps
    setTimestamps(fixtureData);

    // Populate department slicer options from fixture
    populateDepartmentSlicer(fixtureData.distribution_by_department);

    // Render dashboard with full dataset
    renderDashboard(buildFilteredView());
  } catch (err) {
    console.error("Dashboard initialization error:", err);
    const badge = document.getElementById("statusBadge");
    if (badge) {
      badge.textContent = "● DATA LOAD ERROR";
      badge.className = "status-badge status-error";
    }
  }

  // Wire up filter event listeners
  document.getElementById("slicerType").addEventListener("change", onFiltersChanged);
  document.getElementById("slicerStatus").addEventListener("change", onFiltersChanged);
  document.getElementById("slicerDepartment").addEventListener("change", onFiltersChanged);
  document.getElementById("tableSearch").addEventListener("input", onFiltersChanged);

  // Reset filters button
  document.getElementById("btnResetFilters").addEventListener("click", resetFilters);

  // Browser interactive simulation button
  document.getElementById("btnSimulate").addEventListener("click", runBrowserSimulation);

  // Email template preview controls (render default meeting template immediately)
  renderTemplatePreview();
  document.getElementById("templateSelector").addEventListener("change", renderTemplatePreview);
  document.getElementById("btnPreview").addEventListener("click", renderTemplatePreview);

  // Initialize Tab Navigation
  initTabs();
});

let currentFilteredView = null; // Stored for responsive re-renders across tabs

// ============================================================
// TAB NAVIGATION CONTROLLER
// Handles Tab 1 (Overview) vs Tab 2 (Audit Log) vs Tab 3 (Template Preview)
// Keyboard accessible (Left/Right arrow, Home/End, Enter/Space)
// ============================================================
function initTabs() {
  const tabs = Array.from(document.querySelectorAll('.tab-btn[role="tab"]'));
  const panels = Array.from(document.querySelectorAll('.tab-panel[role="tabpanel"]'));

  function switchTab(targetTab) {
    if (!targetTab) return;
    const targetPanelId = targetTab.getAttribute("aria-controls");
    const targetPanel = document.getElementById(targetPanelId);

    // Update tab states
    tabs.forEach(tab => {
      const isActive = tab === targetTab;
      tab.classList.toggle("active", isActive);
      tab.setAttribute("aria-selected", isActive ? "true" : "false");
      tab.setAttribute("tabindex", isActive ? "0" : "-1");
    });

    // Update panel visibility
    panels.forEach(panel => {
      const isTarget = panel === targetPanel;
      panel.classList.toggle("active", isTarget);
      if (isTarget) {
        panel.removeAttribute("hidden");
        panel.style.display = "block";
      } else {
        panel.setAttribute("hidden", "");
        panel.style.display = "none";
      }
    });

    // If switching to Overview, ensure Chart.js canvases resize and render properly
    if (targetPanelId === "tabOverviewPanel") {
      requestAnimationFrame(() => {
        if (donutChart) donutChart.resize();
        if (deptChart) deptChart.resize();
        if (timelineChart) timelineChart.resize();
        // If charts were initialized while container was hidden, re-render to ensure full-fidelity layout
        if (currentFilteredView && (timelineChart?.width === 0 || donutChart?.width === 0)) {
          renderDonutChart(currentFilteredView.distribution_by_status);
          renderDepartmentChart(currentFilteredView.distribution_by_department);
          renderTimelineChart(currentFilteredView.timeline);
        }
      });
    }

    // If switching to template preview, ensure template is updated
    if (targetPanelId === "tabTemplatePanel") {
      renderTemplatePreview();
    }
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => switchTab(tab));

    tab.addEventListener("keydown", (e) => {
      let newIndex = null;
      if (e.key === "ArrowRight") {
        newIndex = (index + 1) % tabs.length;
      } else if (e.key === "ArrowLeft") {
        newIndex = (index - 1 + tabs.length) % tabs.length;
      } else if (e.key === "Home") {
        newIndex = 0;
      } else if (e.key === "End") {
        newIndex = tabs.length - 1;
      }

      if (newIndex !== null) {
        e.preventDefault();
        tabs[newIndex].focus();
        switchTab(tabs[newIndex]);
      }
    });
  });
}

// ============================================================
// TIMESTAMPS
// Distinguishes data snapshot timestamp from client page render time
// ============================================================
function setTimestamps(data) {
  const dataTsEl = document.getElementById("headerTimestamp");
  if (dataTsEl && data?.system_info?.last_updated) {
    const d = new Date(data.system_info.last_updated);
    const dateStr = d.toISOString().slice(0, 10);
    const timeStr = d.toISOString().slice(11, 16);
    dataTsEl.textContent = `Data Snapshot: ${dateStr} ${timeStr} UTC`;
  }

  const renderTsEl = document.getElementById("renderTimestamp");
  if (renderTsEl) {
    renderTsEl.textContent = `Page Loaded: ${new Date().toLocaleTimeString()}`;
  }
}

// ============================================================
// BUILD FILTERED VIEW
// Aggregates dispatches matching current slicer selections.
// Reconciles KPIs, status breakdown, department counts, and timeline
// dynamically from the filtered record population.
// ============================================================
function buildFilteredView() {
  if (!fixtureData) return null;

  const typeFilter   = document.getElementById("slicerType")?.value || "all";
  const statusFilter = document.getElementById("slicerStatus")?.value || "all";
  const deptFilter   = document.getElementById("slicerDepartment")?.value || "all";
  const searchQuery  = (document.getElementById("tableSearch")?.value || "").trim().toLowerCase();

  // Combine session simulations with synthetic fixture dispatches
  const allDispatches = [...sessionSimEntries, ...fixtureData.recent_dispatches];

  // Filter dispatch records based on active slicers and search input
  const filtered = allDispatches.filter(d => {
    const matchType   = typeFilter   === "all" || d.type       === typeFilter;
    const matchStatus = statusFilter === "all" || d.status     === statusFilter;
    const matchDept   = deptFilter   === "all" || d.department === deptFilter;

    let matchSearch = true;
    if (searchQuery !== "") {
      const maskedEmail = maskEmail(d.recipient);
      const searchTarget = `${d.id} ${d.recipient} ${maskedEmail} ${d.department} ${d.type} ${d.subject} ${d.status} ${d.timestamp}`.toLowerCase();
      matchSearch = searchTarget.includes(searchQuery);
    }

    return matchType && matchStatus && matchDept && matchSearch;
  });

  // Calculate status counts from filtered dispatches
  const smtpAccepted   = filtered.filter(d => d.status === "SMTP_ACCEPTED").length;
  const simulated      = filtered.filter(d => d.status === "SIMULATED").length;
  const failed         = filtered.filter(d => d.status === "FAILED").length;
  const requiresReview = filtered.filter(d => d.status === "REQUIRES_REVIEW").length;
  const pending        = filtered.filter(d => d.status === "PENDING").length;

  // SMTP Acceptance Rate:
  //   Numerator:   SMTP_ACCEPTED (MTA handoff confirmed)
  //   Denominator: SMTP_ACCEPTED + FAILED (total live attempts)
  //   SIMULATED, REQUIRES_REVIEW, and PENDING are strictly excluded from both.
  const liveAttempts = smtpAccepted + failed;
  let smtpRateStr = "N/A";
  if (liveAttempts > 0) {
    if (smtpAccepted === 0) {
      smtpRateStr = "0.0%";
    } else {
      smtpRateStr = (smtpAccepted / liveAttempts * 100).toFixed(1) + "%";
    }
  }

  // Tally status distribution for donut chart
  const statusDist = {};
  filtered.forEach(d => {
    statusDist[d.status] = (statusDist[d.status] || 0) + 1;
  });

  // Tally department distribution for bar chart
  const deptDist = {};
  filtered.forEach(d => {
    if (d.department) {
      deptDist[d.department] = (deptDist[d.department] || 0) + 1;
    }
  });

  // Tally chronological timeline from all dispatches
  // Extract all unique dates across the entire dataset to preserve consistent x-axis
  const allDates = Array.from(new Set(allDispatches.map(d => d.timestamp?.slice(0, 10)).filter(Boolean))).sort();
  const dailyCounts = {};
  filtered.forEach(d => {
    if (d.timestamp) {
      const date = d.timestamp.slice(0, 10);
      dailyCounts[date] = (dailyCounts[date] || 0) + 1;
    }
  });

  const timelineData = allDates.map(date => ({
    date: date,
    count: dailyCounts[date] || 0,
  }));

  return {
    kpis: {
      // Global structural metadata (unaffected by slicers)
      total_contacts: fixtureData.kpis.total_contacts,
      total_reminders: fixtureData.kpis.total_reminders,
      // Filter-responsive operational metrics
      smtp_accepted_count: smtpAccepted,
      simulated_count: simulated,
      requires_review_count: requiresReview,
      smtp_acceptance_rate: smtpRateStr,
    },
    distribution_by_status: statusDist,
    distribution_by_department: deptDist,
    timeline: timelineData,
    recent_dispatches: filtered,
    total_records: allDispatches.length,
    matching_records: filtered.length,
  };
}

// ============================================================
// MAIN RENDER ORCHESTRATOR
// ============================================================
function renderDashboard(view) {
  if (!view) return;
  currentFilteredView = view;
  renderKPIs(view.kpis);
  renderDonutChart(view.distribution_by_status);
  renderDepartmentChart(view.distribution_by_department);
  renderTimelineChart(view.timeline);
  renderAuditTable(view.recent_dispatches);
  updateRecordCountBadge(view.matching_records, view.total_records);
}

// ============================================================
// ============================================================
// KPI CARDS
// ============================================================
function renderKPIs(kpis) {
  setText("kpiContacts",     kpis.total_contacts);
  setText("kpiReminders",    kpis.total_reminders);
  setText("kpiSmtpAccepted", kpis.smtp_accepted_count);
  setText("kpiSimulated",    kpis.simulated_count);
  setText("kpiRate",         kpis.smtp_acceptance_rate);
  setText("kpiReview",       kpis.requires_review_count);

  const subEl = document.getElementById("kpiRateSublabel");
  if (subEl) {
    if (kpis.smtp_acceptance_rate === "N/A") {
      subEl.textContent = "No live attempts matching filters";
    } else {
      subEl.textContent = "SMTP_ACCEPTED ÷ live attempts";
    }
  }
}

function updateRecordCountBadge(matching, total) {
  const el = document.getElementById("recordCount");
  if (!el) return;
  if (matching === total) {
    el.textContent = `Showing all ${total} records`;
  } else {
    el.textContent = `Showing ${matching} of ${total} records`;
  }
}

// ============================================================
// DOUGHNUT CHART — Dispatch Status Breakdown
// Center alignment, human-readable labels, center cutout total
// ============================================================
function renderDonutChart(statusDist) {
  const ctx = document.getElementById("chartDonut");
  if (!ctx) return;

  const rawKeys = Object.keys(statusDist);
  const labels  = rawKeys.map(k => STATUS_CONFIG[k]?.label || k);
  const values  = Object.values(statusDist);
  const colors  = rawKeys.map(k => STATUS_CONFIG[k]?.color || PBI.muted);
  const totalCount = values.reduce((sum, v) => sum + v, 0);

  // Center cutout text plugin
  const centerTextPlugin = {
    id: "centerTextPlugin",
    beforeDraw: (chart) => {
      const { width, height, ctx } = chart;
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const centerX = width / 2;
      const centerY = chart.chartArea ? (chart.chartArea.top + chart.chartArea.bottom) / 2 : height / 2;

      ctx.font = "bold 28px 'Segoe UI', -apple-system, sans-serif";
      ctx.fillStyle = "#0F172A";
      ctx.fillText(totalCount, centerX, centerY - 8);

      ctx.font = "600 12px 'Segoe UI', -apple-system, sans-serif";
      ctx.fillStyle = "#475569";
      ctx.fillText(totalCount === 1 ? "DISPATCH" : "DISPATCHES", centerX, centerY + 14);
      ctx.restore();
    }
  };

  if (donutChart) donutChart.destroy();

  // If 0 matching records, render empty state placeholder in chart
  if (rawKeys.length === 0 || totalCount === 0) {
    donutChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["No Data"],
        datasets: [{
          data: [1],
          backgroundColor: ["#E2E8F0"],
          borderWidth: 0,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
        }
      },
      plugins: [centerTextPlugin],
    });
    return;
  }

  donutChart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderWidth: 2,
        borderColor: "#FFFFFF",
        hoverOffset: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "68%",
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            font: { family: "'Segoe UI', sans-serif", size: 12, weight: "500" },
            padding: 12,
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            pointStyle: "circle",
            color: "#334155",
          },
        },
        tooltip: {
          backgroundColor: "#0F172A",
          titleFont: { family: "'Segoe UI'", size: 13, weight: "600" },
          bodyFont: { family: "'Segoe UI'", size: 12.5 },
          padding: 11,
          cornerRadius: 6,
          callbacks: {
            label: (ctx) => ` ${ctx.label}: ${ctx.parsed} (${(ctx.parsed / totalCount * 100).toFixed(1)}%)`,
          },
        },
      },
    },
    plugins: [centerTextPlugin],
  });
}

// ============================================================
// DEPARTMENT HORIZONTAL BAR CHART
// Sorted descending by count, zero baseline, headroom
// ============================================================
function renderDepartmentChart(deptDist) {
  const ctx = document.getElementById("chartDepartment");
  if (!ctx) return;

  // Sort descending by count
  const sorted = Object.entries(deptDist).sort((a, b) => b[1] - a[1]);
  const labels = sorted.map(e => e[0]);
  const values = sorted.map(e => e[1]);
  const maxVal = values.length > 0 ? Math.max(...values) : 0;

  if (deptChart) deptChart.destroy();

  deptChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels.length > 0 ? labels : ["No Data"],
      datasets: [{
        label: "Dispatches",
        data: values.length > 0 ? values : [0],
        backgroundColor: "#118DFF",
        borderRadius: 4,
        barPercentage: 0.65,
        categoryPercentage: 0.8,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: "y",
      scales: {
        x: {
          beginAtZero: true,
          suggestedMax: maxVal > 0 ? maxVal + 1 : 4,
          ticks: {
            stepSize: 1,
            precision: 0,
            font: { family: "'Segoe UI'", size: 12 },
            color: "#475569",
          },
          grid: { color: "#E2E8F0" },
        },
        y: {
          ticks: {
            font: { family: "'Segoe UI'", size: 12.5, weight: "500" },
            color: "#1E293B",
          },
          grid: { display: false },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0F172A",
          titleFont: { family: "'Segoe UI'", size: 13, weight: "600" },
          bodyFont: { family: "'Segoe UI'", size: 12.5 },
          padding: 11,
          cornerRadius: 6,
          callbacks: {
            label: (ctx) => ` Reminders: ${ctx.parsed.x}`,
          },
        },
      },
    },
  });
}

// ============================================================
// TIMELINE VELOCITY LINE CHART
// Spans 100% width of panel (maintainAspectRatio: false),
// filter-responsive daily dispatches, zero baseline, chronological x-axis
// ============================================================
function renderTimelineChart(timeline) {
  const ctx = document.getElementById("chartTimeline");
  if (!ctx || !timeline) return;

  const labels = timeline.map(t => t.date);
  const values = timeline.map(t => t.count);
  const maxVal = values.length > 0 ? Math.max(...values) : 0;

  if (timelineChart) timelineChart.destroy();

  timelineChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        label: "Daily Dispatches",
        data: values,
        fill: true,
        backgroundColor: "rgba(17,141,255,0.12)",
        borderColor: "#118DFF",
        borderWidth: 2.5,
        pointBackgroundColor: "#118DFF",
        pointBorderColor: "#FFFFFF",
        pointBorderWidth: 2,
        pointRadius: 4.5,
        pointHoverRadius: 7,
        tension: 0.35,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false, // Critical: enables 100% container width stretch
      layout: {
        padding: { top: 10, bottom: 5, left: 10, right: 15 },
      },
      scales: {
        x: {
          ticks: {
            font: { family: "'Segoe UI'", size: 12 },
            color: "#475569",
          },
          grid: { color: "#E2E8F0" },
        },
        y: {
          beginAtZero: true,
          suggestedMax: Math.max(maxVal + 1, 4),
          ticks: {
            stepSize: 1,
            precision: 0,
            font: { family: "'Segoe UI'", size: 12 },
            color: "#475569",
          },
          grid: { color: "#E2E8F0" },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0F172A",
          titleFont: { family: "'Segoe UI'", size: 13, weight: "600" },
          bodyFont: { family: "'Segoe UI'", size: 12.5 },
          padding: 11,
          cornerRadius: 6,
          callbacks: {
            label: (ctx) => ` Daily Volume: ${ctx.parsed.y} dispatch${ctx.parsed.y === 1 ? '' : 'es'}`,
          },
        },
      },
    },
  });
}

// ============================================================
// AUDIT TABLE
// Filter-synchronized dispatch log with status pills & empty state
// ============================================================
function renderAuditTable(dispatches) {
  const tbody = document.getElementById("auditTableBody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!dispatches || dispatches.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" class="empty-state-cell">
          <div class="empty-state-content">
            <span class="empty-state-icon">🔍</span>
            <p class="empty-state-title">No matching dispatch records found</p>
            <p class="empty-state-subtitle">No records match the current combination of filters and search query.</p>
            <button class="btn-reset-table" type="button" onclick="document.getElementById('btnResetFilters').click()">↺ Reset Filters</button>
          </div>
        </td>
      </tr>`;
    return;
  }

  dispatches.forEach(d => {
    const tr = document.createElement("tr");
    const statusCfg = STATUS_CONFIG[d.status] || { label: d.status, pill: "pill-pending" };
    const masked = maskEmail(d.recipient);
    tr.innerHTML = `
      <td class="col-id">${esc(d.id)}</td>
      <td class="col-recipient" title="${esc(masked)}">${esc(masked)}</td>
      <td class="col-dept">${esc(d.department)}</td>
      <td class="col-type">${esc(d.type)}</td>
      <td class="col-subject" title="${esc(d.subject)}">${esc(d.subject)}</td>
      <td class="col-status"><span class="status-pill ${statusCfg.pill}">${esc(statusCfg.label)}</span></td>
      <td class="col-timestamp">${esc(d.timestamp)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ============================================================
// DEPARTMENT SLICER
// Dynamically populates distinct departments from fixture
// ============================================================
function populateDepartmentSlicer(deptDist) {
  const sel = document.getElementById("slicerDepartment");
  if (!sel || !deptDist) return;

  const existing = new Set(Array.from(sel.options).map(o => o.value));
  Object.keys(deptDist).forEach(dept => {
    if (!existing.has(dept)) {
      const opt = document.createElement("option");
      opt.value = dept;
      opt.textContent = dept;
      sel.appendChild(opt);
    }
  });
}

// ============================================================
// FILTER HANDLERS
// ============================================================
function onFiltersChanged() {
  if (!fixtureData) return;
  const view = buildFilteredView();
  renderDashboard(view);
}

function resetFilters() {
  const typeSel = document.getElementById("slicerType");
  const statusSel = document.getElementById("slicerStatus");
  const deptSel = document.getElementById("slicerDepartment");
  const searchInput = document.getElementById("tableSearch");

  if (typeSel) typeSel.value = "all";
  if (statusSel) statusSel.value = "all";
  if (deptSel) deptSel.value = "all";
  if (searchInput) searchInput.value = "";

  onFiltersChanged();
}

// ============================================================
// CLIENT-SIDE BROWSER SIMULATION
// Demonstrates live dispatch ingestion without SMTP or backend
// ============================================================
function runBrowserSimulation() {
  const btn = document.getElementById("btnSimulate");
  if (btn) {
    btn.textContent = "⏳ Simulating…";
    btn.disabled = true;
  }

  sessionSimCount++;
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10);
  const timeStr = now.toTimeString().slice(0, 8);

  const demoEntry = {
    id: `SIM-${String(sessionSimCount).padStart(4, "0")}`,
    recipient: "demo.user@example.com",
    department: "Operations",
    type: "Meeting",
    subject: `Browser Demo: Automated Meeting Reminder #${sessionSimCount}`,
    status: "SIMULATED",
    timestamp: `${dateStr} ${timeStr}`,
  };

  // Add only to session array; fixtureData is never modified
  sessionSimEntries.unshift(demoEntry);

  // Re-render dashboard
  const view = buildFilteredView();
  renderDashboard(view);

  setTimeout(() => {
    if (btn) {
      btn.textContent = "▶ Simulate Interactive Batch";
      btn.disabled = false;
    }
  }, 600);
}

// ============================================================
// EMAIL TEMPLATE PREVIEW
// Synthetic template preview using safe static placeholder data
// ============================================================
function renderTemplatePreview() {
  const type  = document.getElementById("templateSelector")?.value || "meeting";
  const frame = document.getElementById("templatePreviewFrame");
  if (!frame) return;

  const s = {
    name:        "Demo User",
    event_title: "Quarterly Business Strategy Review",
    due_date:    "2026-09-30",
    due_time:    "14:00",
    amount_due:  "$3,500.00",
    department:  "Operations",
    company:     "Acme Corporation",
  };

  const templates = {
    meeting: {
      subject: "Meeting Alert: Quarterly Business Strategy Review",
      headerTitle: "Meeting Alert",
      headerSub: "Enterprise Calendar Dispatch & Attendance Confirmation",
      gradient: "linear-gradient(135deg, #0A196F, #118DFF)",
      buttonText: "Join Scheduled Meeting",
      buttonBg: "#118DFF",
      bodyHtml: `
        <p style="margin-bottom:12px;font-size:15px;color:#0F172A;">Hello <strong>${esc(s.name)}</strong>,</p>
        <p style="font-size:14px;color:#334155;line-height:1.6;margin-bottom:16px;">This is an automated operational notification regarding your upcoming session: <strong>${esc(s.event_title)}</strong>.</p>
        <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-left:4px solid #118DFF;padding:14px 18px;margin:16px 0;border-radius:4px;font-size:13.5px;color:#1E293B;line-height:1.7;">
          <div><strong>Scheduled Date:</strong> ${esc(s.due_date)}</div>
          <div><strong>Session Time:</strong> ${esc(s.due_time)}</div>
          <div><strong>Department:</strong> ${esc(s.department)}</div>
        </div>
        <p style="font-size:13px;color:#64748B;line-height:1.5;margin-bottom:20px;">Please ensure your attendance is logged prior to the session start time. For rescheduling inquiries, contact your team lead.</p>
        <div style="text-align:center;margin:22px 0 10px;">
          <span style="display:inline-block;background:#118DFF;color:#FFFFFF;padding:11px 26px;border-radius:6px;font-weight:600;font-size:14px;box-shadow:0 2px 4px rgba(0,0,0,0.12);pointer-events:none;">Join Scheduled Meeting</span>
        </div>
      `,
    },
    payment: {
      subject: "Payment & Invoice Alert: Outstanding Invoice Settlement",
      headerTitle: "Payment & Invoice Alert",
      headerSub: "Finance Operations & Accounts Receivable Notification",
      gradient: "linear-gradient(135deg, #C19B00, #D83B01)",
      buttonText: "View & Settle Invoice",
      buttonBg: "#107C41",
      bodyHtml: `
        <p style="margin-bottom:12px;font-size:15px;color:#0F172A;">Dear <strong>${esc(s.name)}</strong>,</p>
        <p style="font-size:14px;color:#334155;line-height:1.6;margin-bottom:16px;">This is a scheduled reminder regarding settlement for invoice item: <strong>${esc(s.event_title)}</strong>.</p>
        <div style="background:#FFFBEB;border:1px solid #FDE68A;border-left:4px solid #C19B00;padding:14px 18px;margin:16px 0;border-radius:4px;font-size:13.5px;color:#1E293B;line-height:1.7;">
          <div><strong>Total Balance Due:</strong> <span style="font-weight:700;color:#92400E;">${esc(s.amount_due)}</span></div>
          <div><strong>Settlement Due Date:</strong> ${esc(s.due_date)}</div>
          <div><strong>Billing Entity:</strong> ${esc(s.company)}</div>
        </div>
        <p style="font-size:13px;color:#64748B;line-height:1.5;margin-bottom:20px;">Please remit payment via corporate treasury transfer or the authorized procurement portal to prevent processing delays.</p>
        <div style="text-align:center;margin:22px 0 10px;">
          <span style="display:inline-block;background:#107C41;color:#FFFFFF;padding:11px 26px;border-radius:6px;font-weight:600;font-size:14px;box-shadow:0 2px 4px rgba(0,0,0,0.12);pointer-events:none;">View & Settle Invoice</span>
        </div>
      `,
    },
    webinar: {
      subject: "Webinar Alert: Executive Workshop Confirmation",
      headerTitle: "Webinar Alert",
      headerSub: "Executive Training & Enterprise Skills Broadcast",
      gradient: "linear-gradient(135deg, #107C41, #0A196F)",
      buttonText: "Join Webinar Room",
      buttonBg: "#0284C7",
      bodyHtml: `
        <p style="margin-bottom:12px;font-size:15px;color:#0F172A;">Hi <strong>${esc(s.name)}</strong>,</p>
        <p style="font-size:14px;color:#334155;line-height:1.6;margin-bottom:16px;">Your seat has been reserved for the upcoming masterclass broadcast: <strong>${esc(s.event_title)}</strong>.</p>
        <div style="background:#ECFDF5;border:1px solid #A7F3D0;border-left:4px solid #107C41;padding:14px 18px;margin:16px 0;border-radius:4px;font-size:13.5px;color:#1E293B;line-height:1.7;">
          <div><strong>Topic:</strong> ${esc(s.event_title)}</div>
          <div><strong>Broadcast Date:</strong> ${esc(s.due_date)}</div>
          <div><strong>Broadcast Time:</strong> ${esc(s.due_time)}</div>
        </div>
        <p style="font-size:13px;color:#64748B;line-height:1.5;margin-bottom:20px;">The broadcast stream opens 10 minutes prior to scheduled start. Presentation materials will be accessible post-webinar.</p>
        <div style="text-align:center;margin:22px 0 10px;">
          <span style="display:inline-block;background:#0284C7;color:#FFFFFF;padding:11px 26px;border-radius:6px;font-weight:600;font-size:14px;box-shadow:0 2px 4px rgba(0,0,0,0.12);pointer-events:none;">Join Webinar Room</span>
        </div>
      `,
    }
  };

  const tmpl = templates[type] || templates.meeting;

  frame.innerHTML = `
    <!-- Envelope Metadata Header -->
    <div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:8px 8px 0 0;padding:14px 18px;font-size:12.5px;line-height:1.6;color:#475569;">
      <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;">
        <div><strong>From:</strong> Automated Reminder Dispatch &lt;reminders@company.example.com&gt;</div>
        <div style="color:#64748B;">◈ SYNTHETIC FIXTURE</div>
      </div>
      <div><strong>To:</strong> ${esc(s.name)} &lt;employee.demo@example.com&gt;</div>
      <div><strong>Subject:</strong> <span style="font-weight:600;color:#0F172A;">${esc(tmpl.subject)}</span></div>
    </div>
    <!-- Email Body Container -->
    <div style="background:#FFFFFF;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 8px 8px;overflow:hidden;box-shadow:0 2px 6px rgba(15,23,42,0.04);">
      <div style="background:${tmpl.gradient};color:#FFFFFF;padding:20px 24px;">
        <strong style="font-size:18px;letter-spacing:-0.2px;display:block;">${tmpl.headerTitle}</strong>
        <span style="font-size:12.5px;opacity:0.92;">${tmpl.headerSub}</span>
      </div>
      <div style="padding:24px 28px;">
        ${tmpl.bodyHtml}
      </div>
      <div style="background:#F8FAFC;border-top:1px solid #E2E8F0;padding:12px 20px;text-align:center;font-size:12px;color:#64748B;">
        This automated notification was dispatched by Email Automation &amp; Reminder System Demo. Unsubscribe preferences are managed by enterprise policy.
      </div>
    </div>
  `;
}

// ============================================================
// UTILITIES
// ============================================================
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = (val === null || val === undefined) ? "—" : val;
}

/**
 * Sanitizes recipient email addresses for safe public presentation (PII protection).
 * Example: alex.m@example.com -> a***@example.com
 */
function maskEmail(email) {
  if (!email || typeof email !== "string" || !email.includes("@")) {
    return "***";
  }
  const [local, domain] = email.split("@");
  if (local.length <= 1) {
    return `*@${domain}`;
  }
  return `${local[0]}***@${domain}`;
}

/**
 * XSS-safe text escaping using DOM text nodes
 */
function esc(str) {
  if (str === null || str === undefined) return "";
  const div = document.createElement("div");
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
