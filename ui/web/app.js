/* -*- coding: utf-8 -*-
 * 简小松·脚本调度助手 · PyWebView 前端主控
 */
(function () {
  "use strict";

  const AUTO_START_DELAY_SECONDS = 5;

  const REPORT_LABELS = {
    success: "日志确认完成",
    failed: "执行失败",
    unknown: "结果未确认",
    stopped: "已停止",
    running: "执行中",
  };

  const state = {
    api: null,
    page: "dashboard",
    config: null,
    options: {
      waitModes: [],
      timeoutActions: [],
      concurrentPolicies: [],
    },
    scriptReports: {},
    runner: {
      isRunning: false,
      isPaused: false,
      statusText: "空闲待命",
      currentTask: "准备就绪",
      progress: { current: 0, total: 0 },
      elapsed: 0,
      elapsedText: "00:00",
    },
    stats: { rows: [], totalRuns: 0, scriptCount: 0, lastTime: "—" },
    logs: [],
    selectedTaskIdx: 0,
    dragSourceIdx: -1,
    updateMode: "check",
    updateItems: [],
    updateBusy: false,
    updateCanceling: false,
    shutdownTimerId: null,
    shutdownRemaining: 0,
    dirtyConfig: false,
    bootstrapped: false,
  };

  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function showToast(message, tone = "info") {
    const stack = $("toastStack");
    if (!stack || !message) return;
    const item = document.createElement("div");
    item.className = `toast ${tone === "ok" ? "ok" : tone === "error" ? "error" : ""}`;
    item.textContent = String(message);
    stack.appendChild(item);
    setTimeout(() => {
      item.style.opacity = "0";
      item.style.transform = "translateY(6px)";
      item.style.transition = "opacity 0.2s, transform 0.2s";
      setTimeout(() => item.remove(), 220);
    }, 3200);
  }

  function formatDuration(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60);
    return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  }

  const taskIcons = new Map();
  let taskIconTimer;

  function executableIconKey(task) {
    return JSON.stringify([task.script_path.trim(), task.use_args ? (task.args || "").trim() : ""]);
  }

  function refreshTaskIcons() {
    clearTimeout(taskIconTimer);
    if (!state.api?.get_task_icons) return;
    taskIconTimer = setTimeout(async () => {
      const pending = new Map();
      for (const task of state.config?.tasks || []) {
        if (!task.script_path?.trim()) continue;
        const key = executableIconKey(task);
        if (taskIcons.has(key)) continue;
        pending.set(key, { path: task.script_path.trim(), args: task.use_args ? (task.args || "").trim() : "" });
      }
      if (!pending.size) return;
      const keys = Array.from(pending.keys());
      keys.forEach((key) => taskIcons.set(key, ""));
      try {
        const icons = await state.api.get_task_icons(Array.from(pending.values()));
        icons.forEach((icon, i) => taskIcons.set(keys[i], icon));
        if (icons.some(Boolean)) renderTaskQueue(false);
      } catch (_) {
        keys.forEach((key) => taskIcons.delete(key));
      }
    }, 240);
  }

  function guessGameIcon(task) {
    const executable = task?.script_path?.trim() && taskIcons.get(executableIconKey(task));
    if (executable) return executable;
    const text = `${task?.name || ""} ${task?.script_path || ""}`.toLowerCase();
    if (/鸣潮|wuwa|ok-ww/.test(text)) return "/assets/games/wuwa_app.png";
    if (/星穹|崩铁|hsr|march7th/.test(text)) return "/assets/games/hsr_app.png";
    if (/绝区零|zzz|onedragon/.test(text)) return "/assets/games/zzz_app.png";
    if (/原神|genshin|bettergi/.test(text)) return "/assets/games/genshin_app.png";
    if (/maaend|终末地/.test(text)) return "/assets/games/maaend_app.png";
    if (/\bmaa\b|明日方舟|maaassistantarknights/.test(text)) return "/assets/games/maa_app.png";
    if (/\bm9a\b|重返未来/.test(text)) return "/assets/games/m9a_app.png";
    return null;
  }

  function guessCardAura(task) {
    const text = `${task?.name || ""} ${task?.script_path || ""}`.toLowerCase();
    if (/鸣潮|wuwa|ok-ww/.test(text)) return "#2dd4bf";
    if (/星穹|崩铁|hsr|march7th/.test(text)) return "#a78bfa";
    if (/绝区零|zzz|onedragon/.test(text)) return "#f59e0b";
    if (/原神|genshin|bettergi/.test(text)) return "#38bdf8";
    return "#60a5fa";
  }

  function shortGameLabel(task) {
    const raw = String(task?.name || "任务").trim().replace(/\.exe$/i, "");
    if (/onedragon/i.test(raw)) return "OneDragon";
    if (/march7th/i.test(raw)) return "March7th";
    return raw;
  }

  function monogramText(name) {
    const clean = String(name || "TS").replace(/[^\w\u4e00-\u9fa5]/g, "");
    if (!clean) return "JS";
    return clean.slice(0, 2).toUpperCase();
  }

  function queueIconMarkup(task) {
    const icon = guessGameIcon(task);
    return icon
      ? `<img class="q-icon" src="${icon}" alt="" />`
      : `<div class="q-monogram">${escapeHtml(monogramText(task.name))}</div>`;
  }

  // -------------------- 页面导航与主题 --------------------

  function switchPage(pageName) {
    state.page = pageName;
    document.querySelectorAll(".nav-item[data-page]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.page === pageName);
    });
    document.querySelectorAll(".page").forEach((sec) => {
      sec.classList.toggle("active", sec.id === `page-${pageName}`);
    });
    if (pageName === "stats") {
      refreshStats();
    } else if (pageName === "logs") {
      scrollLogsToBottom();
    } else if (pageName === "settings") {
      updateDailySchedule();
    }
  }

  function applyTheme(themeName) {
    const theme = String(themeName || "jiansz").toLowerCase();
    document.body.setAttribute("data-theme", theme);
    const wallpaper = $("backdropWallpaper");
    if (wallpaper) {
      wallpaper.style.opacity = theme === "light" ? "0.10" : theme === "dark" ? "0.08" : "0.24";
    }
  }

  function enabledTasks() {
    return (state.config?.tasks || []).filter((task) => task.enabled)
      .sort((a, b) => (a.order || 0) - (b.order || 0));
  }

  function parseMinutesSeconds(text) {
    const match = String(text || "").match(/(\d+):(\d{2})/);
    if (!match) return null;
    return Number(match[1]) * 60 + Number(match[2]);
  }

  function renderScriptReports() {
    const grid = $("gamesGrid");
    const tasks = enabledTasks();
    grid.dataset.count = String(tasks.length);
    grid.dataset.overflow = tasks.length > 5 ? "true" : "false";

    const counts = { success: 0, unknown: 0, failed: 0, stopped: 0, pending: 0 };
    grid.innerHTML = tasks.map((task, idx) => {
      const rawName = String(task.name || "未命名任务").trim();
      const titleText = rawName.replace(/\.exe$/i, "") || rawName;
      const scriptFile = String(task.script_path || "").split(/[\\/]/).pop() || "";
      const scriptStem = scriptFile.replace(/\.exe$/i, "");
      const report = state.scriptReports[task.task_id];
      const subText = report?.source
        ? report.source
        : scriptFile && scriptStem.toLowerCase() !== titleText.toLowerCase()
        ? scriptFile
        : "";
      const icon = guessGameIcon(task);
      const aura = guessCardAura(task);
      const result = report?.result || "empty";
      if (result === "success") counts.success += 1;
      else if (result === "failed") counts.failed += 1;
      else if (result === "stopped") counts.stopped += 1;
      else if (result === "unknown") counts.unknown += 1;
      else counts.pending += 1;

      const dailyResult = report?.status?.startsWith("每日委托");
      const label = dailyResult ? ({success: "每日达成", failed: "每日目标未达成", unknown: "每日待确认"}[result]) : (REPORT_LABELS[result] || "今日待执行");
      const detail = report?.status || (result === "running"
        ? "脚本正在运行，结束后核对本轮日志。"
        : "今天还没有运行记录");
      const elapsedText = report?.elapsed || "—";
      const elapsedSec = parseMinutesSeconds(elapsedText);
      const limitSec = task.timeout_minutes > 0 ? task.timeout_minutes * 60 : 0;
      const pct = elapsedSec !== null && limitSec > 0
        ? Math.max(4, Math.min(100, Math.round((elapsedSec / limitSec) * 100)))
        : elapsedSec !== null
        ? 100
        : 0;
      const limitBadge = limitSec > 0 ? `/ ${task.timeout_minutes}:00` : "已耗时";
      const pctBadge = elapsedSec !== null && limitSec > 0 ? `${pct}%` : "";
      const fullTime = String(report?.time || "");
      const compactTime = fullTime ? fullTime.replace(/^\d{4}-/, "") : "今日暂无记录";
      const clickableAttrs = report
        ? `data-report="${escapeHtml(task.task_id)}" role="button" tabindex="0"`
        : "";
      return `
        <article class="game-card ${report ? "clickable" : ""}" ${clickableAttrs}>
          <div class="game-card-aura" style="background:${aura}"></div>
          ${icon ? `<img class="game-card-watermark" src="${icon}" alt="" aria-hidden="true" />` : ""}
          <header class="gc-header">
            ${icon ? `<img class="gc-app-icon" src="${icon}" alt="" />`
              : `<div class="gc-app-icon gc-monogram" aria-hidden="true">${escapeHtml(monogramText(task.name))}</div>`}
            <div class="gc-title-col">
              <div class="gc-name" title="${escapeHtml(rawName)}">${escapeHtml(titleText)}</div>
              ${subText ? `<div class="report-script" title="${escapeHtml(scriptFile || subText)}">${escapeHtml(subText)}</div>` : ""}
            </div>
            <span class="gc-order-tag">#${idx + 1}</span>
          </header>
          <div class="report-status-row">
            <span class="report-status ${escapeHtml(result)}"><span class="rs-dot"></span>${escapeHtml(label)}</span>
            ${pctBadge ? `<span class="report-pct-badge" title="耗时占超时上限比例">${pctBadge}</span>` : ""}
          </div>
          <div class="gc-metric-panel">
            <div class="gc-metric-head">
              <strong class="gc-metric-num">${escapeHtml(elapsedText)}</strong>
              <span class="gc-metric-cap">${escapeHtml(limitBadge)}</span>
            </div>
            <div class="gc-bar-track">
              <div class="gc-bar-fill ${escapeHtml(result)}" style="width:${pct}%"></div>
            </div>
          </div>
          <p class="report-evidence" title="${escapeHtml(detail)}">${escapeHtml(detail)}</p>
          <footer class="report-footer">
            <span class="report-time-wrap" title="${escapeHtml(fullTime)}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
              <span>${escapeHtml(compactTime)}</span>
            </span>
            ${report ? `<span class="report-more-hint">详情 ›</span>` : `<span>待命</span>`}
          </footer>
        </article>`;
    }).join("");

    const summaryEl = $("dockSummaryPills");
    if (summaryEl) {
      if (!tasks.length) {
        summaryEl.innerHTML = `<span class="summary-pill">未启用任何任务</span>`;
      } else {
        const pills = [];
        if (counts.success) pills.push(`<span class="summary-pill ok"><strong>${counts.success}</strong> 确认完成</span>`);
        if (counts.unknown) pills.push(`<span class="summary-pill unknown"><strong>${counts.unknown}</strong> 未确认</span>`);
        if (counts.failed) pills.push(`<span class="summary-pill fail"><strong>${counts.failed}</strong> 执行失败</span>`);
        if (counts.stopped) pills.push(`<span class="summary-pill"><strong>${counts.stopped}</strong> 已停止</span>`);
        if (counts.pending) pills.push(`<span class="summary-pill"><strong>${counts.pending}</strong> 今日待执行</span>`);
        summaryEl.innerHTML = pills.join("");
      }
    }

    if (!tasks.length) {
      grid.innerHTML = `<div class="reports-empty">暂无已启用的任务<button type="button" class="btn-subtle" data-open-tasks>前往任务调度</button></div>`;
    }
  }

  function showScriptReport(report, name = report?.taskName) {
    if (!report) return;
    $("scriptReportTitle").textContent = (name || "未命名任务") + " · 运行报告";
    $("scriptReportState").textContent = REPORT_LABELS[report.result] || "暂无报告";
    $("scriptReportEvidence").textContent = report.status || (report.result === "running"
      ? "正在等待本轮日志结果。" : "本次运行没有记录详细结果。");
    $("scriptReportMeta").textContent = [report.time, report.elapsed ? "耗时 " + report.elapsed : "",
      report.source ? "日志 " + report.source : ""].filter(Boolean).join(" · ");
    openModal("scriptReportModal");
  }

  async function showScriptReportHistory() {
    const list = $("scriptReportHistoryList");
    list.textContent = "正在读取报告…";
    openModal("scriptReportHistoryModal");
    try {
      const reports = await state.api.get_script_report_history();
      const tasks = new Map((state.config?.tasks || []).map((task) => [task.task_id, task]));
      const rows = document.createDocumentFragment();
      for (const report of reports) {
        const task = tasks.get(report.taskId);
        const name = task?.name || report.taskName || "未命名任务";
        const row = document.createElement("button");
        row.type = "button";
        row.className = "btn-subtle report-history-row";
        const stateLabel = task && !task.enabled ? "（已停用）" : "";
        row.innerHTML = `<span>${escapeHtml(name + stateLabel)}</span>
          <span>${escapeHtml([report.time, REPORT_LABELS[report.result]].filter(Boolean).join(" · "))}</span>`;
        row.addEventListener("click", () => {
          closeModal("scriptReportHistoryModal");
          showScriptReport(report, name);
        });
        rows.appendChild(row);
      }
      list.replaceChildren(rows);
      if (!reports.length) list.textContent = "暂无脚本运行报告。";
    } catch (_) {
      list.textContent = "读取脚本运行报告失败。";
    }
  }

  // -------------------- 控制坞、流水线与遥测 --------------------

  function renderDashboardSummary() {
    const tasks = state.config?.tasks || [];
    const enabled = enabledTasks();
    renderScriptReports();

    const heroCount = $("heroEnabledCount");
    if (heroCount) heroCount.textContent = `${enabled.length} / ${tasks.length}`;
    const navBadge = $("navTaskBadge");
    if (navBadge) navBadge.textContent = String(enabled.length);

    const strip = $("pipelineStrip");
    if (strip) {
      if (!enabled.length) {
        strip.innerHTML = `<span class="pipe-empty">暂无已启用任务 · 点击前往任务调度开启</span>`;
      } else {
        const visible = enabled.slice(0, 5);
        const parts = visible.map((t) => {
          const icon = guessGameIcon(t);
          const iconHtml = icon ? `<img src="${icon}" alt="" />` : "";
          return `<span class="pipe-chip">${iconHtml}<span>${escapeHtml(shortGameLabel(t))}</span></span>`;
        });
        let html = parts.join(`<span class="pipe-arrow">→</span>`);
        if (enabled.length > visible.length) {
          html += `<span class="pipe-arrow">+${enabled.length - visible.length}</span>`;
        }
        strip.innerHTML = html;
      }
    }

    const qUpdates = $("quickCheckUpdates");
    if (qUpdates && state.config) {
      qUpdates.checked = true;
      qUpdates.disabled = true;
    }
    const qShutdown = $("quickShutdownAfter");
    if (qShutdown && state.config) {
      qShutdown.checked = Boolean(state.config.shutdown_after_done);
    }
  }

  function renderRunnerState() {
    const r = state.runner;
    const dockIdle = $("dockIdle");
    const dockRunning = $("dockRunning");
    if (dockIdle && dockRunning) {
      dockIdle.style.display = r.isRunning ? "none" : "flex";
      dockRunning.style.display = r.isRunning ? "flex" : "none";
    }

    const dot = $("sidebarDot");
    const sText = $("sidebarStatusText");
    if (dot) {
      dot.classList.toggle("running", Boolean(r.isRunning && !r.isPaused));
      dot.classList.toggle("paused", Boolean(r.isRunning && r.isPaused));
    }
    if (sText) {
      sText.textContent = r.isRunning
        ? `${r.isPaused ? "已暂停" : "运行中"} · ${r.elapsedText || formatDuration(r.elapsed)}`
        : r.statusText || "空闲待命";
    }

    const badge = $("runStatusBadge");
    if (badge) {
      badge.textContent = r.isPaused ? "已暂停" : r.statusText || "正在执行";
      badge.classList.toggle("paused", Boolean(r.isPaused));
    }
    const label = $("runTaskLabel");
    if (label) label.textContent = r.currentTask || "准备就绪";

    const cur = r.progress?.current || 0;
    const tot = r.progress?.total || 0;
    const pct = tot > 0 ? Math.min(100, Math.round((cur / tot) * 100)) : 0;
    const pText = $("runProgressText");
    if (pText) pText.textContent = `${cur} / ${tot} (${pct}%)`;
    const pBar = $("runProgressBar");
    if (pBar) pBar.style.width = `${pct}%`;
    const eText = $("runElapsedText");
    if (eText) eText.textContent = r.elapsedText || formatDuration(r.elapsed);

    const btnPause = $("btnPauseTasks");
    const btnResume = $("btnResumeTasks");
    if (btnPause && btnResume) {
      btnPause.style.display = r.isPaused ? "none" : "inline-flex";
      btnResume.style.display = r.isPaused ? "inline-flex" : "none";
    }
  }

  // -------------------- 任务调度 Master-Detail & Drag --------------------

  function renderTaskQueue(refreshDetail = true) {
    const listEl = $("taskQueueList");
    const countEl = $("tasksQueueCount");
    const tasks = state.config?.tasks || [];
    const enabledCount = tasks.filter((t) => t.enabled).length;
    if (countEl) countEl.textContent = `${enabledCount} / ${tasks.length}`;
    if (!listEl) return;

    if (state.selectedTaskIdx >= tasks.length) {
      state.selectedTaskIdx = Math.max(0, tasks.length - 1);
    }

    if (!tasks.length) {
      listEl.innerHTML = `<div class="empty-detail" style="padding:28px 12px">暂无任务，点击右上角「+ 添加」</div>`;
      renderTaskDetail();
      renderDashboardSummary();
      return;
    }

    listEl.innerHTML = tasks
      .map((t, idx) => {
        const subText = `#${t.order}`;
        return `
          <div class="queue-item ${idx === state.selectedTaskIdx ? "selected" : ""}" draggable="true" data-idx="${idx}">
            <span class="drag-grip" title="拖动调整顺序">⋮⋮</span>
            ${queueIconMarkup(t)}
            <div class="q-meta">
              <div class="q-name">${escapeHtml(t.name || "未命名任务")}</div>
              <div class="q-sub">${escapeHtml(subText)}</div>
            </div>
            <label class="mini-switch" onclick="event.stopPropagation()">
              <input type="checkbox" class="q-toggle" data-idx="${idx}" ${t.enabled ? "checked" : ""} />
              <span class="switch-track"></span>
            </label>
          </div>`;
      })
      .join("");

    if (refreshDetail) renderTaskDetail();
    renderDashboardSummary();
    refreshTaskIcons();
  }

  function renderTaskDetail() {
    const tasks = state.config?.tasks || [];
    const emptyEl = $("taskDetailEmpty");
    const contentEl = $("taskDetailContent");
    if (!emptyEl || !contentEl) return;

    if (!tasks.length || state.selectedTaskIdx < 0 || state.selectedTaskIdx >= tasks.length) {
      emptyEl.style.display = "block";
      contentEl.style.display = "none";
      return;
    }
    emptyEl.style.display = "none";
    contentEl.style.display = "block";

    const t = tasks[state.selectedTaskIdx];
    const kwJoin = (arr) => (Array.isArray(arr) ? arr.join(", ") : String(arr || ""));
    $("fieldTaskName").value = t.name || "";
    $("fieldScriptPath").value = t.script_path || "";
    $("fieldUseArgs").checked = Boolean(t.use_args);
    $("fieldArgs").value = t.args || "";
    $("fieldArgs").disabled = !t.use_args;
    $("fieldOrder").value = Number(t.order ?? state.selectedTaskIdx + 1);
    $("fieldTimeout").value = Number(t.timeout_minutes ?? 30);
    $("fieldTimeoutAction").value = t.timeout_action || "kill_and_continue";
    $("fieldEnterDelay").value = Number(t.confirm_enter_delay_seconds ?? 0);
    $("fieldWaitMode").value = t.wait_mode || "direct_process";
    $("fieldWaitProcess").value = t.wait_process_name || "";
    $("fieldWatchdog").checked = Boolean(t.enable_watchdog);
    $("fieldConcurrentGroup").value = t.concurrent_group || "";
    $("fieldConcurrentPolicy").value = t.concurrent_policy || "wait_all";
    $("fieldProcessKeywords").value = kwJoin(t.process_keywords);
    $("fieldWindowKeywords").value = kwJoin(t.window_keywords);
    $("fieldMonitorGrace").value = Number(t.monitor_absent_grace_seconds ?? 15);
  }

  function bindTaskDetailListeners() {
    const mapField = (id, prop, transform = (v) => v, isCheckbox = false) => {
      const el = $(id);
      if (!el) return;
      const evt = el.tagName === "SELECT" || isCheckbox ? "change" : "input";
      el.addEventListener(evt, () => {
        const tasks = state.config?.tasks || [];
        const t = tasks[state.selectedTaskIdx];
        if (!t) return;
        const val = isCheckbox ? el.checked : el.value;
        t[prop] = transform(val);
        if (prop === "use_args") {
          $("fieldArgs").disabled = !t.use_args;
        }
        state.dirtyConfig = true;
        if (["name", "script_path", "use_args", "args"].includes(prop)) refreshTaskIcons();
        if (["name", "script_path", "use_args", "args", "timeout_minutes", "order"].includes(prop)) {
          // 轻量同步队列项显示，避免重建导致输入失焦
          const item = document.querySelector(`.queue-item[data-idx="${state.selectedTaskIdx}"]`);
          if (item) {
            const nameEl = item.querySelector(".q-name");
            const subEl = item.querySelector(".q-sub");
            if (nameEl) nameEl.textContent = t.name || "未命名任务";
            const icon = item.querySelector(".q-icon, .q-monogram");
            if (icon) icon.outerHTML = queueIconMarkup(t);
            if (subEl) {
              subEl.textContent = `#${t.order}`;
            }
          }
          renderDashboardSummary();
        }
      });
    };

    const toInt = (def = 0, min = 0) => (v) => {
      const n = parseInt(v, 10);
      return Number.isFinite(n) ? Math.max(min, n) : def;
    };
    const toKwList = (v) =>
      String(v || "")
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean);

    mapField("fieldTaskName", "name");
    mapField("fieldScriptPath", "script_path");
    mapField("fieldUseArgs", "use_args", Boolean, true);
    mapField("fieldArgs", "args");
    mapField("fieldOrder", "order", toInt(1, 1));
    mapField("fieldTimeout", "timeout_minutes", toInt(30, 0));
    mapField("fieldTimeoutAction", "timeout_action");
    mapField("fieldEnterDelay", "confirm_enter_delay_seconds", toInt(0, 0));
    mapField("fieldWaitMode", "wait_mode");
    mapField("fieldWaitProcess", "wait_process_name");
    mapField("fieldWatchdog", "enable_watchdog", Boolean, true);
    mapField("fieldConcurrentGroup", "concurrent_group");
    mapField("fieldConcurrentPolicy", "concurrent_policy");
    mapField("fieldProcessKeywords", "process_keywords", toKwList);
    mapField("fieldWindowKeywords", "window_keywords", toKwList);
    mapField("fieldMonitorGrace", "monitor_absent_grace_seconds", toInt(15, 1));
  }

  function normalizeTaskOrders() {
    const tasks = state.config?.tasks || [];
    tasks.forEach((t, idx) => {
      t.order = idx + 1;
    });
  }

  function addNewTask() {
    if (!state.config) return;
    const tasks = state.config.tasks;
    tasks.push({
      task_id: crypto.randomUUID().replace(/-/g, ""),
      name: `新任务 ${tasks.length + 1}`,
      script_path: "",
      args: "",
      use_args: false,
      enabled: true,
      order: tasks.length + 1,
      timeout_minutes: 30,
      timeout_action: "kill_and_continue",
      confirm_enter_delay_seconds: 0,
      wait_mode: "direct_process",
      wait_process_name: "",
      concurrent_group: "",
      concurrent_policy: "wait_all",
      enable_watchdog: false,
      process_keywords: [],
      window_keywords: [],
      monitor_absent_grace_seconds: 15,
    });
    state.selectedTaskIdx = tasks.length - 1;
    state.dirtyConfig = true;
    renderTaskQueue();
  }

  function deleteSelectedTask() {
    const tasks = state.config?.tasks || [];
    if (!tasks.length || state.selectedTaskIdx < 0 || state.selectedTaskIdx >= tasks.length) return;
    const [removed] = tasks.splice(state.selectedTaskIdx, 1);
    normalizeTaskOrders();
    state.selectedTaskIdx = Math.max(0, Math.min(state.selectedTaskIdx, tasks.length - 1));
    state.dirtyConfig = true;
    renderTaskQueue();
    showToast(`已移除任务「${removed?.name || ""}」（点击保存后生效）`);
  }

  // -------------------- 设置与配置持久化 --------------------

  function renderSettings() {
    const cfg = state.config;
    if (!cfg) return;
    $("setWinStartup").checked = Boolean(cfg.windows_startup);
    $("setAutoStart").checked = Boolean(cfg.auto_start_tasks);
    $("setTimeoutShot").checked = Boolean(cfg.enable_timeout_screenshot);
    $("setCheckUpdates").checked = true;
    $("setCheckUpdates").disabled = true;
    $("setShutdownAfter").checked = Boolean(cfg.shutdown_after_done);
    $("setShutdownDelay").value = Number(cfg.shutdown_delay_seconds ?? 60);
    $("setAutoExit").checked = Boolean(cfg.auto_exit_after_done);
    $("setCloseToTray").checked = Boolean(cfg.close_to_tray);
    $("setThemeSelect").value = cfg.theme || "jiansz";
  }

  async function updateDailySchedule(save = false) {
    if (!state.api?.get_daily_schedule) return;
    const button = $("btnSaveDailySchedule");
    const enabled = $("dailyScheduleEnabled");
    const time = $("dailyScheduleTime");
    const status = $("dailyScheduleStatus");
    if (button.dataset.busy) return;
    if (save && enabled.checked && !time.value) {
      showToast("请选择每日启动时间", "error");
      return;
    }
    button.dataset.busy = "true";
    button.disabled = enabled.disabled = time.disabled = true;
    status.textContent = save ? "正在保存…" : "正在读取 Windows 计划任务…";
    let editable = save;
    try {
      const result = save ? await state.api.save_daily_schedule(enabled.checked, time.value)
                          : await state.api.get_daily_schedule();
      if (!result.ok) {
        status.textContent = result.error || "无法读取定时启动设置。";
        return;
      }
      editable = true;
      enabled.checked = result.enabled;
      time.value = result.time;
      status.textContent = result.enabled
        ? (result.nextRun ? `下次启动：${result.nextRun}` : `每天 ${result.time} 启动`)
        : "定时启动已关闭";
      if (save) showToast("定时启动设置已保存", "ok");
    } catch (_) {
      status.textContent = "无法连接 Windows 任务计划程序。";
    } finally {
      delete button.dataset.busy;
      button.disabled = enabled.disabled = !editable;
      time.disabled = !editable || !enabled.checked;
    }
  }

  async function checkAppUpdate() {
    const button = $("btnCheckAppUpdate");
    const status = $("appReleaseStatus");
    const releases = $("btnAppReleases");
    button.disabled = true;
    releases.hidden = true;
    status.textContent = "正在检查简小松更新…";
    try {
      const result = await state.api.check_app_update();
      const messages = {
        available: `发现新版本 ${result.latest}，当前版本 ${result.current}`,
        current: `当前版本 ${result.current} 已是最新稳定版`,
        no_release: `当前版本 ${result.current}，官方暂未发布稳定版`,
        error: "暂时无法检查更新，请稍后重试",
      };
      status.textContent = messages[result.state] || messages.error;
      releases.hidden = !["available", "no_release"].includes(result.state);
    } catch (_) {
      status.textContent = "暂时无法检查更新，请稍后重试";
    } finally {
      button.disabled = false;
    }
  }

  function collectCurrentConfigPayload() {
    if (!state.config) return null;
    // 确保按当前顺序字段重排（如果用户手动改过 order 数字）
    const tasks = [...state.config.tasks].sort((a, b) => (a.order || 0) - (b.order || 0));
    state.config.tasks = tasks;
    return {
      ...state.config,
      tasks,
    };
  }

  async function saveMailSettings(test) {
    if (!state.api) return;
    const buttons = [$("btnSaveMail"), $("btnTestMail")];
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const saved = await state.api.save_mail_alert_settings(
        $("mailEnabled").checked, $("mailSender").value, $("mailRecipient").value, $("mailCode").value,
      );
      if (!saved.ok) {
        showToast(saved.error, "error");
        return;
      }
      $("mailCode").value = "";
      if (test) {
        const result = await state.api.test_mail_alert();
        showToast(result.ok ? "测试邮件已提交，请检查收件箱。" : result.error, result.ok ? "ok" : "error");
      } else {
        showToast("邮件设置已保存", "ok");
      }
    } catch (_) {
      showToast("邮件设置操作失败。", "error");
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  async function saveConfigToBackend(silent = false) {
    if (!state.api || !state.config) return false;
    const payload = collectCurrentConfigPayload();
    const res = await state.api.save_config(payload);
    if (!res?.ok) {
      if (!silent) showToast(res?.error || "保存配置失败", "error");
      return false;
    }
    if (res.config) {
      state.config = res.config;
      renderTaskQueue();
      renderSettings();
      renderDashboardSummary();
    }
    state.dirtyConfig = false;
    if (!silent) showToast("配置已保存到 config.json", "ok");
    return true;
  }

  // -------------------- 统计与日志 --------------------

  function renderStats() {
    const st = state.stats || { rows: [], totalRuns: 0, scriptCount: 0, lastTime: "—" };
    $("kpiTotalRuns").textContent = `${st.totalRuns || 0} 次`;
    $("kpiScriptCount").textContent = `${st.scriptCount || 0} 个`;
    $("kpiLastTime").textContent = st.lastTime || "—";

    const tbody = $("statsTableBody");
    const emptyEl = $("statsEmpty");
    if (!tbody || !emptyEl) return;

    const rows = st.rows || [];
    if (!rows.length) {
      tbody.innerHTML = "";
      emptyEl.style.display = "block";
      return;
    }
    emptyEl.style.display = "none";
    tbody.innerHTML = rows
      .map(
        (r) => `
        <tr>
          <td>${escapeHtml(r.name)}</td>
          <td class="num">${escapeHtml(r.count)} 次</td>
          <td class="num">${escapeHtml(r.averageText)}</td>
          <td class="num">${escapeHtml(r.lastText)}</td>
          <td class="num">${escapeHtml(r.lastTime || "—")}</td>
          <td title="${escapeHtml(r.lastStatus || "")}">${escapeHtml(String(r.lastStatus || "—").split("：", 1)[0])}</td>
        </tr>`
      )
      .join("");
  }

  async function refreshStats() {
    if (!state.api) return;
    const st = await state.api.get_stats();
    if (st) {
      state.stats = st;
      renderStats();
    }
  }

  function classifyLogLine(line) {
    const s = String(line || "");
    if (/={4,}|调度流程启动|后台执行引擎/.test(s)) return "session";
    if (/失败|异常|错误|超时|强杀|强制停止|过早退出|ERROR/i.test(s)) return "err";
    if (/警告|重试|验证|暂停|取消|⚠/.test(s)) return "warn";
    if (/完成|成功|正常结束|已是最新|已连接/.test(s)) return "ok";
    return "";
  }

  function renderLogs() {
    const term = $("logTerminal");
    if (!term) return;
    const keyword = ($("logFilterInput")?.value || "").trim().toLowerCase();
    const filtered = keyword
      ? state.logs.filter((l) => l.toLowerCase().includes(keyword))
      : state.logs;
    term.innerHTML = filtered
      .map((l) => `<div class="log-line ${classifyLogLine(l)}">${escapeHtml(l)}</div>`)
      .join("");
    if ($("logAutoScroll")?.checked) {
      term.scrollTop = term.scrollHeight;
    }
  }

  function appendLogLine(line) {
    state.logs.push(line);
    if (state.logs.length > 2000) {
      state.logs.splice(0, state.logs.length - 2000);
    }
    const term = $("logTerminal");
    if (!term) return;
    const keyword = ($("logFilterInput")?.value || "").trim().toLowerCase();
    if (keyword && !line.toLowerCase().includes(keyword)) return;
    const div = document.createElement("div");
    div.className = `log-line ${classifyLogLine(line)}`;
    div.textContent = line;
    term.appendChild(div);
    while (term.childElementCount > 2000) {
      term.removeChild(term.firstElementChild);
    }
    if ($("logAutoScroll")?.checked) {
      term.scrollTop = term.scrollHeight;
    }
  }

  function scrollLogsToBottom() {
    const term = $("logTerminal");
    if (term && $("logAutoScroll")?.checked) {
      term.scrollTop = term.scrollHeight;
    }
  }

  // -------------------- 弹窗层：报告、更新、异常、关机 --------------------

  let scriptCatalogTimer;
  let autoStartTimer;

  function openModal(id) {
    const el = $(id);
    if (el) el.style.display = "grid";
  }

  function closeModal(id) {
    if (id === "scriptCatalogModal") clearInterval(scriptCatalogTimer);
    const el = $(id);
    if (el) el.style.display = "none";
  }

  function showScriptCatalogNotice(waitSeconds) {
    const skip = $("skipScriptCatalogNotice");
    const confirm = $("btnConfirmScriptCatalog");
    const deadline = performance.now() + waitSeconds * 1000;
    clearInterval(scriptCatalogTimer);
    skip.checked = false;
    const updateCountdown = () => {
      const seconds = Math.max(0, Math.ceil((deadline - performance.now()) / 1000));
      skip.disabled = confirm.disabled = seconds > 0;
      confirm.textContent = seconds ? `前往 Mirror酱（${seconds} 秒）` : "前往 Mirror酱";
      if (!seconds) clearInterval(scriptCatalogTimer);
    };
    scriptCatalogTimer = setInterval(updateCountdown, 200);
    updateCountdown();
    openModal("scriptCatalogModal");
    $("scriptCatalogModal").querySelector(".modal-footer [data-close-modal]").focus();
  }

  async function openScriptCatalog(confirmed = false) {
    if (!state.api) return;
    const button = $(confirmed ? "btnConfirmScriptCatalog" : "btnGetScripts");
    const skip = $("skipScriptCatalogNotice");
    if (button.disabled) return;
    button.disabled = true;
    try {
      const result = await state.api.open_script_catalog(confirmed, confirmed && skip.checked);
      if (result.noticeRequired) {
        showScriptCatalogNotice(result.waitSeconds);
      } else if (result.ok) {
        closeModal("scriptCatalogModal");
        if (result.warning) showToast(result.warning, "error");
      } else {
        showToast(result.error || "打开脚本列表失败", "error");
      }
    } catch (_) {
      showToast("打开脚本列表失败，请稍后重试", "error");
    } finally {
      button.disabled = false;
    }
  }

  async function openUpdateModal(mode = "check") {
    if (!state.api) return;
    if (state.updateBusy) { openModal("updateModal"); return; }
    state.updateMode = mode;
    $("updateModalTitle").textContent = mode === "prepare" ? "启动前检查脚本更新" : "脚本与工具版本管理";
    $("updateModalHint").textContent =
      mode === "prepare"
        ? "正在扫描并检查所有已启用任务对应的脚本，就绪后自动开始一条龙。"
        : "勾选需要检查或更新的脚本程序。";
    $("updateLogBox").innerHTML = "";
    $("updateTableBody").innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">正在扫描本地脚本…</td></tr>`;
    showUpdateProgress(null, {phase:"检查版本",message:"正在读取启用脚本版本"});
    openModal("updateModal");

    const res = await state.api.updates_discover(mode === "prepare");
    state.updateItems = res?.items || [];
    renderUpdateTable();
    if (res?.busy) { setUpdateBusy(true); return; }

    if (!state.updateItems.length && mode !== "prepare") {
      $("updateTableBody").innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">没有匹配到可检查的脚本。</td></tr>`;
      return;
    }
    const indices = state.updateItems.map((_, i) => i);
    await startUpdate(mode, indices);
  }

  function setUpdateBusy(busy) {
    state.updateBusy = busy;
    for (const id of ["btnUpdateCheck","btnUpdateRun","btnUpdateOpenTool","updCheckAll"]) $(id).disabled = busy;
    document.querySelectorAll(".upd-row-check").forEach(el => el.disabled = busy);
    $("btnUpdateCancel").disabled = state.updateCanceling && busy;
    $("btnUpdateCancel").textContent = busy ? (state.updateCanceling ? "正在取消…" : "取消更新") : "关闭";
    $("btnUpdateClose").title = busy ? "取消本次更新，保留已下载部分" : "关闭";
    $("btnUpdateRun").textContent = state.updateMode === "prepare" ? "重试检查并更新" : "更新所选脚本";
  }

  async function startUpdate(mode, indices) {
    if (state.updateBusy) return;
    state.updateMode = mode;
    state.updateCanceling = false;
    setUpdateBusy(true);
    const res = await state.api.updates_start(mode, indices);
    if (!res?.ok) { setUpdateBusy(false); showToast(res?.error || "更新启动失败", "error"); }
  }

  function progressText(progress) {
    if (!progress) return "等待检查";
    const percent = Number.isFinite(progress.percent) ? `${Math.floor(progress.percent)}%` : "";
    const size = progress.unit === "bytes" ? `${(progress.completed / 1048576).toFixed(1)} MB${progress.total ? ` / ${(progress.total / 1048576).toFixed(1)} MB` : ""}` : "";
    const speed = progress.speed > 0 ? `${(progress.speed / 1048576).toFixed(2)} MB/s` : "";
    return [progress.phase, percent, size, speed].filter(Boolean).join(" · ");
  }

  function progressCell(item) {
    const p = item.progress;
    if (!p) return escapeHtml(item.status || "等待检查");
    const active = !["已取消","更新失败","检查失败"].includes(item.status);
    const percent = Number.isFinite(p.percent) ? Math.max(0,Math.min(100,p.percent)) : null;
    return `<div>${escapeHtml(progressText(p))}</div><div class="update-progress-track ${percent === null && active ? "indeterminate" : ""}"><div style="width:${percent ?? 0}%"></div></div>`;
  }

  function showUpdateProgress(item, progress, active = true) {
    const p = progress || {};
    const percent = Number.isFinite(p.percent) ? Math.max(0,Math.min(100,p.percent)) : null;
    $("updateProgressTitle").textContent = [item?.name,p.phase || "等待检查"].filter(Boolean).join(" · ");
    $("updateProgressPercent").textContent = percent === null ? (active ? "进行中" : "—") : `${Math.floor(percent)}%`;
    $("updateProgressTrack").classList.toggle("indeterminate", active && percent === null);
    $("updateProgressFill").style.width = `${percent ?? 0}%`;
    if (percent === null) $("updateProgressTrack").removeAttribute("aria-valuenow");
    else $("updateProgressTrack").setAttribute("aria-valuenow",String(percent));
    $("updateProgressDetail").textContent = [progressText(p),p.message].filter(Boolean).join("；");
  }

  function renderUpdateTable() {
    const tbody = $("updateTableBody");
    if (!tbody) return;
    const hadRows = Boolean(tbody.querySelector(".upd-row-check"));
    const checked = new Set(Array.from(tbody.querySelectorAll(".upd-row-check:checked"), el => Number(el.dataset.idx)));
    tbody.innerHTML = state.updateItems
      .map(
        (item, idx) => `
        <tr>
          <td><input type="checkbox" class="upd-row-check" data-idx="${idx}" ${!hadRows || checked.has(idx) ? "checked" : ""} ${state.updateBusy ? "disabled" : ""} /></td>
          <td title="${escapeHtml(item.detail || "")}">${escapeHtml(item.name)}</td>
          <td class="mono-sm">${escapeHtml(item.current || "—")}</td>
          <td class="mono-sm">${escapeHtml(item.latest || "—")}</td>
          <td>${escapeHtml(item.status || "待检查")}</td>
          <td class="update-row-progress" data-progress-index="${idx}">${progressCell(item)}</td>
        </tr>`
      )
      .join("");
  }

  function showAbnormalModal(report) {
    const events = report?.events || [];
    if (!events.length) return;
    $("abnormalList").innerHTML = events
      .map(
        (ev) => `
        <div class="abnormal-item">
          <div class="ab-time">${escapeHtml(ev.time || "")}${ev.task ? ` · ${escapeHtml(ev.task)}` : ""}</div>
          <div>${escapeHtml(ev.message || "")}</div>
        </div>`
      )
      .join("");
    openModal("abnormalModal");
  }

  function showShutdownWarningModal(seconds, deadline) {
    const finishAt = Number(deadline) * 1000 || Date.now() + (Number(seconds) || 60) * 1000;
    state.shutdownRemaining = Math.max(0, Math.ceil((finishAt - Date.now()) / 1000));
    if (state.shutdownTimerId) clearInterval(state.shutdownTimerId);
    $("shutdownCountdownNum").textContent = `${state.shutdownRemaining}s`;
    if ($("btnCancelShutdownIdle")) $("btnCancelShutdownIdle").style.display = "inline-flex";
    openModal("shutdownModal");
    state.shutdownTimerId = setInterval(() => {
      state.shutdownRemaining = Math.max(0, Math.ceil((finishAt - Date.now()) / 1000));
      if (state.shutdownRemaining <= 0) {
        clearInterval(state.shutdownTimerId);
        state.shutdownTimerId = null;
        $("shutdownCountdownNum").textContent = "0s";
        if ($("btnCancelShutdownIdle")) $("btnCancelShutdownIdle").style.display = "none";
        return;
      }
      $("shutdownCountdownNum").textContent = `${state.shutdownRemaining}s`;
    }, 1000);
  }

  async function cancelShutdownAction() {
    if (!state.api) return;
    const res = await state.api.cancel_shutdown();
    if (!res?.ok) {
      showToast(res?.error || "取消关机失败", "error");
      return;
    }
    if (state.shutdownTimerId) {
      clearInterval(state.shutdownTimerId);
      state.shutdownTimerId = null;
    }
    if ($("btnCancelShutdownIdle")) $("btnCancelShutdownIdle").style.display = "none";
    closeModal("shutdownModal");
    showToast(res.message || "已取消自动关机", "ok");
  }

  function cancelAutoStartCountdown() {
    const dialog = $("autoStartDialog");
    if (!dialog.open) return false;
    clearInterval(autoStartTimer);
    dialog.close();
    return true;
  }

  function showAutoStartCountdown() {
    const dialog = $("autoStartDialog");
    const ring = $("autoStartRing");
    const seconds = $("autoStartSeconds");
    const duration = AUTO_START_DELAY_SECONDS * 1000;
    const deadline = performance.now() + duration;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const tick = () => {
      const remaining = Math.max(0, deadline - performance.now());
      const wholeSeconds = Math.ceil(remaining / 1000);
      const ringTime = reducedMotion ? wholeSeconds * 1000 : remaining;
      ring.style.setProperty("--auto-start-progress", `${ringTime / duration * 100}%`);
      const label = `${wholeSeconds} 秒`;
      if (seconds.textContent !== label) seconds.textContent = label;
      if (!remaining) startTasksFlow();
    };

    dialog.showModal();
    autoStartTimer = setInterval(tick, 50);
    tick();
  }

  // -------------------- 任务启动流程 --------------------

  async function startTasksFlow(skipUpdates = false) {
    cancelAutoStartCountdown();
    if (!state.api || !state.config) return;
    if (state.runner.isRunning) {
      showToast("任务已经在运行中");
      return;
    }
    const payload = collectCurrentConfigPayload();
    if (state.updateBusy) { openModal("updateModal"); return; }
    const res = await state.api.start_tasks({ ...payload, skip_updates: skipUpdates });
    if (res?.needPrepareUpdate) { await openUpdateModal("prepare"); return; }
    if (!res?.ok) {
      showToast(res?.error || "启动任务失败", "error");
      return;
    }
    state.dirtyConfig = false;
  }

  // -------------------- 后端事件处理 --------------------

  function handleBackendEvent(ev) {
    if (!ev || !ev.type) return;
    const { type, payload } = ev;

    switch (type) {
      case "log":
        if (payload?.line) appendLogLine(payload.line);
        break;
      case "script_report":
        if (payload?.key && payload.report) {
          state.scriptReports[payload.key] = payload.report;
          renderScriptReports();
        }
        break;
      case "script_reports_reset":
        state.scriptReports = payload || {};
        renderScriptReports();
        break;
      case "runner":
        state.runner = { ...state.runner, ...payload };
        if (state.runner.isRunning) cancelAutoStartCountdown();
        renderRunnerState();
        break;
      case "toast":
        showToast(payload?.message, payload?.tone || "info");
        break;
      case "stats":
        if (payload) {
          state.stats = payload;
          renderStats();
        }
        break;
      case "shutdown_warning":
        cancelAutoStartCountdown();
        showShutdownWarningModal(payload?.seconds, payload?.deadline);
        break;
      case "update_item":
        if (payload && typeof payload.index === "number" && state.updateItems[payload.index]) {
          state.updateItems[payload.index] = payload;
          renderUpdateTable();
        }
        break;
      case "update_busy":
        if (payload?.busy) cancelAutoStartCountdown();
        if (payload?.agentLaunch) {
          state.updateMode = "prepare";
          state.updateCanceling = false;
          $("updateModalTitle").textContent = "启动前检查脚本更新";
          $("updateModalHint").textContent = "收到本机启动指令，脚本就绪后自动开始一条龙。";
          $("updateLogBox").innerHTML = "";
          showUpdateProgress(null, {phase:"检查版本"});
          openModal("updateModal");
        }
        if (Array.isArray(payload?.items)) {
          state.updateItems = payload.items;
          renderUpdateTable();
        }
        setUpdateBusy(Boolean(payload?.busy));
        break;
      case "update_progress": {
        const item = state.updateItems[payload?.index];
        if (item && payload.progress) {
          item.progress = payload.progress;
          item.status = "正在更新";
          const cell = document.querySelector(`[data-progress-index="${payload.index}"]`);
          if (cell) cell.innerHTML = progressCell(item);
          showUpdateProgress(item, payload.progress);
        }
        break;
      }
      case "update_log":
        if (payload?.line) {
          const box = $("updateLogBox");
          if (box) {
            const div = document.createElement("div");
            div.className = `log-line ${classifyLogLine(payload.line)}`;
            div.textContent = payload.line;
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
          }
        }
        break;
      case "update_done":
        state.updateCanceling = false;
        setUpdateBusy(false);
        if (payload?.canceled) {
          const message = "已取消更新，下载进度已保留";
          showUpdateProgress(null, {phase: message}, false);
          $("updateModalHint").textContent = message;
          break;
        }
        const deferred = payload?.deferred || [];
        const inAppUpdates = payload?.inAppUpdates || [];
        const updateMessages = [
          deferred.length ? `${deferred.join("、")}更新未完成，沿用本地版本` : "",
          inAppUpdates.length ? `发现 ${inAppUpdates.join("、")}，请在 MAA 内更新` : "",
        ].filter(Boolean).join("；");
        showUpdateProgress(null, {phase: updateMessages || (payload?.ready ? "全部就绪" : "检查 / 更新已结束"),
                                 ...(payload?.ready ? {percent:100} : {})}, false);
        if (payload?.mode === "prepare") {
          if (payload.ready) {
            closeModal("updateModal");
            const skipped = payload.unsupported || [];
            showToast(updateMessages || (skipped.length ? `已完成可用的版本检查，未适配：${skipped.join("、")}` : "脚本更新检查完成，开始执行任务"), "ok");
            if (!payload.agentLaunch) startTasksFlow(true);
          } else {
            $("updateModalHint").textContent = "脚本未全部就绪，任务尚未启动。请查看原因后点击“重试检查并更新”。";
          }
        } else {
          showToast(updateMessages || (payload?.ready ? "脚本检查 / 更新已完成" : "检查 / 更新结束，请查看状态和日志"), payload?.ready ? "ok" : "error");
        }
        break;
      default:
        break;
    }
  }

  async function pumpBackendEvents() {
    if (!state.api) return;
    try {
      const items = await state.api.drain_events();
      if (Array.isArray(items) && items.length) {
        items.forEach(handleBackendEvent);
      }
    } catch (_) {
      // 忽略窗口关闭瞬间的 IPC 异常
    }
  }

  // Expose pump hook for Python evaluate_js
  window.__jianszPump = function () {
    pumpBackendEvents();
  };

  // -------------------- UI 事件绑定 --------------------

  function bindGlobalUiEvents() {
    // 窗口控制
    $("btnWinMin")?.addEventListener("click", () => state.api?.win_minimize());
    $("btnWinMax")?.addEventListener("click", () => state.api?.win_toggle_maximize());
    $("btnWinClose")?.addEventListener("click", () => state.api?.win_request_close());

    // 侧边栏导航
    document.querySelectorAll(".nav-item[data-page]").forEach((btn) => {
      btn.addEventListener("click", () => switchPage(btn.dataset.page));
    });

    // 外部链接
    document.querySelectorAll("[data-external]").forEach((btn) => {
      btn.addEventListener("click", () => state.api?.open_external(btn.dataset.external));
    });

    $("gamesGrid")?.addEventListener("click", (e) => {
      const card = e.target.closest("[data-report]");
      if (card) {
        const key = card.dataset.report;
        const task = enabledTasks().find((item) => item.task_id === key);
        showScriptReport(state.scriptReports[key], task?.name);
      }
      if (e.target.closest("[data-open-tasks]")) switchPage("tasks");
    });

    $("gamesGrid")?.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const card = e.target.closest("[data-report]");
      if (!card) return;
      e.preventDefault();
      const key = card.dataset.report;
      const task = enabledTasks().find((item) => item.task_id === key);
      showScriptReport(state.scriptReports[key], task?.name);
    });

    $("btnHeroReports")?.addEventListener("click", showScriptReportHistory);
    $("btnHeroUpdates")?.addEventListener("click", () => openUpdateModal("check"));
    $("btnGetScripts")?.addEventListener("click", () => openScriptCatalog());
    $("btnConfirmScriptCatalog")?.addEventListener("click", () => openScriptCatalog(true));
    $("pipelineStrip")?.addEventListener("click", () => switchPage("tasks"));
    $("btnStartTasks")?.addEventListener("click", () => startTasksFlow(false));
    $("btnTasksStart")?.addEventListener("click", () => {
      switchPage("dashboard");
      startTasksFlow(false);
    });
    $("btnPauseTasks")?.addEventListener("click", () => state.api?.pause_tasks());
    $("btnResumeTasks")?.addEventListener("click", () => state.api?.resume_tasks());
    $("btnStopTasks")?.addEventListener("click", () => state.api?.stop_tasks());
    $("btnEmergencyStop")?.addEventListener("click", () => state.api?.emergency_stop_tasks());
    $("btnCancelShutdown")?.addEventListener("click", cancelShutdownAction);
    $("btnCancelShutdownIdle")?.addEventListener("click", cancelShutdownAction);

    // 快捷胶囊同步到设置与配置
    $("quickCheckUpdates")?.addEventListener("change", (e) => {
      if (!state.config) return;
      state.config.auto_update_scripts_before_launch = Boolean(e.target.checked);
      renderSettings();
      saveConfigToBackend(true);
    });
    $("quickShutdownAfter")?.addEventListener("change", (e) => {
      if (!state.config) return;
      state.config.shutdown_after_done = Boolean(e.target.checked);
      renderSettings();
      saveConfigToBackend(true);
    });

    // 任务队列点击、启用开关、拖拽排序
    const queueList = $("taskQueueList");
    if (queueList) {
      queueList.addEventListener("click", (e) => {
        const item = e.target.closest(".queue-item");
        if (!item) return;
        state.selectedTaskIdx = Number(item.dataset.idx);
        renderTaskQueue();
      });

      queueList.addEventListener("change", (e) => {
        if (!e.target.classList.contains("q-toggle")) return;
        const idx = Number(e.target.dataset.idx);
        const tasks = state.config?.tasks || [];
        if (tasks[idx]) {
          tasks[idx].enabled = Boolean(e.target.checked);
          state.dirtyConfig = true;
          const enabledCount = tasks.filter((t) => t.enabled).length;
          $("tasksQueueCount").textContent = `${enabledCount} / ${tasks.length}`;
          renderDashboardSummary();
        }
      });

      queueList.addEventListener("dragstart", (e) => {
        const item = e.target.closest(".queue-item");
        if (!item) return;
        state.dragSourceIdx = Number(item.dataset.idx);
        e.dataTransfer.effectAllowed = "move";
      });

      queueList.addEventListener("dragover", (e) => {
        const item = e.target.closest(".queue-item");
        if (!item) return;
        e.preventDefault();
        document.querySelectorAll(".queue-item.drag-over").forEach((el) => el.classList.remove("drag-over"));
        item.classList.add("drag-over");
      });

      queueList.addEventListener("dragleave", (e) => {
        const item = e.target.closest(".queue-item");
        if (item) item.classList.remove("drag-over");
      });

      queueList.addEventListener("drop", (e) => {
        e.preventDefault();
        document.querySelectorAll(".queue-item.drag-over").forEach((el) => el.classList.remove("drag-over"));
        const target = e.target.closest(".queue-item");
        if (!target || state.dragSourceIdx < 0) return;
        const dropIdx = Number(target.dataset.idx);
        const tasks = state.config?.tasks || [];
        if (dropIdx === state.dragSourceIdx || !tasks[state.dragSourceIdx]) return;
        const [moved] = tasks.splice(state.dragSourceIdx, 1);
        tasks.splice(dropIdx, 0, moved);
        normalizeTaskOrders();
        state.selectedTaskIdx = dropIdx;
        state.dragSourceIdx = -1;
        state.dirtyConfig = true;
        renderTaskQueue();
      });

      queueList.addEventListener("dragend", () => {
        state.dragSourceIdx = -1;
        document.querySelectorAll(".queue-item.drag-over").forEach((el) => el.classList.remove("drag-over"));
      });
    }

    $("btnAddTask")?.addEventListener("click", addNewTask);
    $("btnDeleteTask")?.addEventListener("click", deleteSelectedTask);
    $("btnSaveConfig")?.addEventListener("click", () => saveConfigToBackend(false));

    $("btnBrowseScript")?.addEventListener("click", async () => {
      if (!state.api) return;
      const res = await state.api.browse_script_file();
      if (res?.ok && res.path) {
        const tasks = state.config?.tasks || [];
        const t = tasks[state.selectedTaskIdx];
        if (t) {
          t.script_path = res.path;
          if (!t.name || t.name.startsWith("新任务")) {
            const parts = res.path.replace(/\\/g, "/").split("/");
            t.name = parts[parts.length - 1] || t.name;
          }
          state.dirtyConfig = true;
          renderTaskQueue();
        }
      }
    });

    $("btnImportConfig")?.addEventListener("click", async () => {
      if (!state.api) return;
      const res = await state.api.import_config();
      if (res?.ok && res.config) {
        state.config = res.config;
        state.scriptReports = res.scriptReports || {};
        state.selectedTaskIdx = 0;
        applyTheme(state.config.theme);
        renderTaskQueue();
        renderSettings();
        renderDashboardSummary();
        showToast("已导入配置方案并应用到当前环境", "ok");
      } else if (res?.error) {
        showToast(res.error, "error");
      }
    });

    $("btnExportConfig")?.addEventListener("click", async () => {
      if (!state.api) return;
      await saveConfigToBackend(true);
      const res = await state.api.export_config();
      if (res?.ok) {
        showToast(`配置已导出至 ${res.path}`, "ok");
      } else if (res?.error) {
        showToast(res.error, "error");
      }
    });

    bindTaskDetailListeners();

    // 统计页与日志页
    $("btnRefreshStats")?.addEventListener("click", refreshStats);
    $("btnScriptReportHistory")?.addEventListener("click", showScriptReportHistory);
    $("btnOpenStatsDir")?.addEventListener("click", () => state.api?.open_folder("stats"));
    $("logFilterInput")?.addEventListener("input", renderLogs);
    $("btnClearLogs")?.addEventListener("click", async () => {
      await state.api?.clear_logs();
      state.logs = [];
      renderLogs();
    });
    $("btnCopyLogs")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(state.logs.join("\n"));
        showToast("已复制全部日志到剪贴板", "ok");
      } catch (_) {
        showToast("剪贴板写入失败，请手动选中复制", "error");
      }
    });
    $("btnOpenLogsDir")?.addEventListener("click", () => state.api?.open_folder("logs"));

    // 设置页（即时保存）
    const bindSetting = (id, key, isNum = false) => {
      const el = $(id);
      if (!el) return;
      el.addEventListener("change", () => {
        if (!state.config) return;
        if (isNum) {
          const n = parseInt(el.value, 10);
          state.config[key] = Number.isFinite(n) ? Math.max(0, n) : 60;
        } else if (el.tagName === "SELECT") {
          state.config[key] = el.value;
          if (key === "theme") applyTheme(el.value);
        } else {
          state.config[key] = Boolean(el.checked);
        }
        renderDashboardSummary();
        saveConfigToBackend(true);
      });
    };

    bindSetting("setWinStartup", "windows_startup");
    bindSetting("setAutoStart", "auto_start_tasks");
    bindSetting("setTimeoutShot", "enable_timeout_screenshot");
    bindSetting("setCheckUpdates", "auto_update_scripts_before_launch");
    bindSetting("setShutdownAfter", "shutdown_after_done");
    bindSetting("setShutdownDelay", "shutdown_delay_seconds", true);
    bindSetting("setAutoExit", "auto_exit_after_done");
    bindSetting("setCloseToTray", "close_to_tray");
    bindSetting("setThemeSelect", "theme");
    $("btnOpenUpdatesSettings")?.addEventListener("click", () => openUpdateModal("check"));
    $("dailyScheduleEnabled")?.addEventListener("change", (e) => { $("dailyScheduleTime").disabled = !e.target.checked; });
    $("btnSaveDailySchedule")?.addEventListener("click", () => updateDailySchedule(true));
    $("btnCheckAppUpdate")?.addEventListener("click", checkAppUpdate);
    $("btnAboutApp")?.addEventListener("click", () => openModal("aboutAppModal"));
    $("btnSaveMail")?.addEventListener("click", () => saveMailSettings(false));
    $("btnTestMail")?.addEventListener("click", () => saveMailSettings(true));

    // 弹窗事件
    document.querySelectorAll("[data-close-modal]").forEach((btn) => {
      btn.addEventListener("click", () => closeModal(btn.dataset.closeModal));
    });
    // 更新器弹窗
    $("updCheckAll")?.addEventListener("change", (e) => {
      const checked = Boolean(e.target.checked);
      document.querySelectorAll(".upd-row-check").forEach((cb) => {
        cb.checked = checked;
      });
    });
    const getSelectedUpdateIndices = () =>
      Array.from(document.querySelectorAll(".upd-row-check:checked")).map((cb) => Number(cb.dataset.idx));
    $("btnUpdateCheck")?.addEventListener("click", () => startUpdate("check", getSelectedUpdateIndices()));
    $("btnUpdateRun")?.addEventListener("click", () => startUpdate(state.updateMode === "prepare" ? "prepare" : "update", getSelectedUpdateIndices()));
    $("btnUpdateOpenTool")?.addEventListener("click", async () => {
      const indices = getSelectedUpdateIndices();
      if (!indices.length) return showToast("请先勾选至少一个脚本", "error");
      const res = await state.api?.updates_open_tool(indices[0]);
      if (res && !res.ok) showToast(res.error || "打开失败", "error");
    });
    const closeUpdate = () => {
      if (state.updateBusy) {
        state.updateCanceling = true;
        setUpdateBusy(true);
        $("updateModalHint").textContent = "正在取消本次更新，已下载部分会保留；等待当前网络操作返回后停止。";
        state.api?.updates_stop();
        return;
      }
      closeModal("updateModal");
    };
    $("btnUpdateClose")?.addEventListener("click", closeUpdate);
    $("btnUpdateCancel")?.addEventListener("click", closeUpdate);

    // 异常报告与关机倒计时弹窗
    const dismissAbnormal = () => {
      state.api?.dismiss_abnormal_report();
      closeModal("abnormalModal");
    };
    $("btnAbnormalDismiss")?.addEventListener("click", dismissAbnormal);
    $("btnAbnormalOk")?.addEventListener("click", dismissAbnormal);
    $("btnAbnormalOpenLogs")?.addEventListener("click", () => state.api?.open_folder("logs"));
    $("btnCancelShutdownModal")?.addEventListener("click", cancelShutdownAction);

    $("btnCancelAutoStart").addEventListener("click", cancelAutoStartCountdown);
    $("autoStartDialog").addEventListener("cancel", (e) => {
      e.preventDefault();
      cancelAutoStartCountdown();
    });

    // 快捷键
    window.addEventListener("keydown", (e) => {
      if (e.key === "F5") {
        e.preventDefault();
        startTasksFlow(false);
      } else if (e.key === "F6") {
        e.preventDefault();
        state.api?.resume_tasks();
      } else if (e.key === "F7") {
        e.preventDefault();
        state.api?.pause_tasks();
      } else if (e.key === "F8") {
        e.preventDefault();
        if (cancelAutoStartCountdown()) return;
        if (e.ctrlKey && e.altKey) state.api?.emergency_stop_tasks();
        else state.api?.stop_tasks();
      } else if ((e.key === "q" || e.key === "Q") && e.ctrlKey) {
        e.preventDefault();
        cancelAutoStartCountdown();
        state.api?.win_close();
      }
    });
  }

  // -------------------- 初始化入口 --------------------

  function populateSelectOptions() {
    const fillSelect = (id, items) => {
      const sel = $(id);
      if (!sel || !items) return;
      const entries = Array.isArray(items)
        ? items.map((opt) => [opt, opt])
        : typeof items === "object"
        ? Object.entries(items)
        : [];
      sel.innerHTML = entries
        .map(([val, label]) => `<option value="${escapeHtml(val)}">${escapeHtml(label)}</option>`)
        .join("");
    };
    fillSelect("fieldWaitMode", state.options.waitModes);
    fillSelect("fieldTimeoutAction", state.options.timeoutActions);
    fillSelect("fieldConcurrentPolicy", state.options.concurrentPolicies);
  }

  async function initApp() {
    if (state.bootstrapped || !window.pywebview?.api) return;
    state.bootstrapped = true;
    state.api = window.pywebview.api;

    try {
      const boot = await state.api.get_bootstrap();
      if (!boot) return;
      state.config = boot.config;
      $("aboutAppVersion").textContent = boot.appVersion || "";
      $("appVersionBadge").textContent = boot.appVersion || "";
      if (boot.appName) document.title = boot.appName;
      state.options = boot.options || state.options;
      state.scriptReports = boot.scriptReports || {};
      state.runner = boot.runner || state.runner;
      state.stats = boot.stats || state.stats;
      state.logs = Array.isArray(boot.logs) ? boot.logs : [];

      populateSelectOptions();
      applyTheme(state.config?.theme || "jiansz");
      renderDashboardSummary();
      renderRunnerState();
      renderTaskQueue();
      renderStats();
      renderLogs();
      renderSettings();

      if (state.api.get_mail_alert_settings) {
        const mail = await state.api.get_mail_alert_settings();
        $("mailEnabled").checked = mail.enabled;
        $("mailSender").value = mail.sender;
        $("mailRecipient").value = mail.recipient;
      }

      if (boot.abnormalReport?.events?.length) {
        showAbnormalModal(boot.abnormalReport);
      }

      if (boot.autoStartOnOpen && !state.runner.isRunning && enabledTasks().length) {
        showAutoStartCountdown();
      }

      setInterval(pumpBackendEvents, 220);
    } catch (err) {
      showToast(`前端初始化失败: ${err}`, "error");
    }
  }

  bindGlobalUiEvents();
  window.addEventListener("pywebviewready", initApp);
  if (window.pywebview?.api) {
    initApp();
  }
})();
