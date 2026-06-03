const STORAGE_KEY = "sth-month-dashboard-data-v3";
// === STH PORTAL INTEGRATION ===
// Все данные хранятся на сервере (FastAPI + SQLite), localStorage не используется.
const API_BASE = "/tasks/api";
function _portalToken() {
  // Auth-скрипт (scripts/inject_auth.py) сохраняет токен под ключом 'p_auth'.
  try { return sessionStorage.getItem("p_auth") || ""; } catch { return ""; }
}
async function _apiGet(path) {
  const r = await fetch(API_BASE + path, { cache: "no-store" });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}
async function _apiPut(path, body) {
  const r = await fetch(API_BASE + path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-Auth-Token": _portalToken() },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PUT ${path} → ${r.status}`);
  return r.json();
}
let _saveTimer = null;
function _scheduleSave(name, payload) {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    _apiPut(name, payload).catch((e) => console.error("save failed:", e));
  }, 600);
}

const state = {
  view: "dashboard",
  monthId: null,
  compareMonthId: null,
  division: "all",
  projectId: "all",
  search: "",
  issueFilter: "all",
  editProjectId: null,
  data: null,
  norms: null,
  actions: [],
};

const views = [
  ["dashboard", "Дашборд"],
  ["report", "Таблица-отчет"],
  ["projects", "Проекты"],
  ["project", "Карточка проекта"],
  ["dynamics", "Динамика"],
  ["funnel", "Воронка подбора"],
  ["marketing", "Маркетинг"],
  ["actions", "Проблемы и действия"],
  ["norms", "Нормативы"],
];

const metricLabels = {
  request: "Заявка от клиента",
  avgOutput: "Суточный выход",
  currentStaff: "Текущий штат",
  productivity: "Производительность",
  penalties: "Штрафные санкции",
  penaltyShare: "% штрафов от выручки",
  revenueVat: "Выручка с НДС",
  requestCloseRate: "% закрытия заявки",
  secretCheck: "Проверка СУПР",
  realizationPayroll: "ФОТ реализации",
  realizationDrivers: "Драйверы роста реализации",
  realizationBlockers: "Блокеры реализации",
  recruitingPayroll: "ФОТ подбора",
  invited: "Приглашенных",
  invitedToResponseRate: "% приглашенных от откликов",
  registered: "Оформленных",
  registeredToInvitedRate: "% оформленных",
  warehouseReached: "Дошедшие до склада",
  warehouseToRegisteredRate: "% дошедших",
  firstShift: "Вышедшие на 1 смену",
  firstShiftToWarehouseRate: "% первой смены",
  tenShifts: "Вышедшие на 10 смен",
  tenShiftsToFirstShiftRate: "% десяти смен",
  recruitingDrivers: "Драйверы роста подбора",
  recruitingBlockers: "Блокеры подбора",
  responseToWarehouseRate: "Отклик → склад",
  marketingProjectName: "Название в медиаплане",
  marketingPayroll: "ФОТ маркетинга",
  marketingBudget: "Бюджет маркетинга",
  responses: "Отклики",
  targetLeads: "Целевые",
  targetLeadRate: "% целевых",
  marketingDrivers: "Драйверы роста маркетинга",
  marketingBlockers: "Блокеры маркетинга",
  responseCost: "Стоимость отклика",
  targetLeadCost: "Стоимость целевого",
  agreements: "Итоговые договоренности и действия",
};

const sourceColumns = [
  { key: "division", label: "Дивизион", type: "text", fixed: true },
  { key: "name", label: "Проект", type: "text", fixed: true },
  { key: "health", label: "Индекс", type: "formula" },
  { key: "request", group: "Реализация", type: "number" },
  { key: "avgOutput", group: "Реализация", type: "number" },
  { key: "currentStaff", group: "Реализация", type: "number" },
  { key: "productivity", group: "Реализация", type: "number" },
  { key: "penalties", group: "Реализация", type: "number" },
  { key: "penaltyShare", group: "Реализация", type: "formula" },
  { key: "revenueVat", group: "Реализация", type: "number" },
  { key: "requestCloseRate", group: "Реализация", type: "formula" },
  { key: "secretCheck", group: "Реализация", type: "number" },
  { key: "realizationPayroll", group: "Реализация", type: "number" },
  { key: "realizationDrivers", group: "Реализация", type: "textarea" },
  { key: "realizationBlockers", group: "Реализация", type: "textarea" },
  { key: "recruitingPayroll", group: "Подбор", type: "number" },
  { key: "invited", group: "Подбор", type: "number" },
  { key: "invitedToResponseRate", group: "Подбор", type: "formula" },
  { key: "registered", group: "Подбор", type: "number" },
  { key: "registeredToInvitedRate", group: "Подбор", type: "formula" },
  { key: "warehouseReached", group: "Подбор", type: "number" },
  { key: "warehouseToRegisteredRate", group: "Подбор", type: "formula" },
  { key: "firstShift", group: "Подбор", type: "number" },
  { key: "firstShiftToWarehouseRate", group: "Подбор", type: "formula" },
  { key: "tenShifts", group: "Подбор", type: "number" },
  { key: "tenShiftsToFirstShiftRate", group: "Подбор", type: "formula" },
  { key: "recruitingDrivers", group: "Подбор", type: "textarea" },
  { key: "recruitingBlockers", group: "Подбор", type: "textarea" },
  { key: "responseToWarehouseRate", group: "Подбор", type: "formula" },
  { key: "marketingProjectName", group: "Маркетинг", type: "text" },
  { key: "marketingPayroll", group: "Маркетинг", type: "number" },
  { key: "marketingBudget", group: "Маркетинг", type: "number" },
  { key: "responses", group: "Маркетинг", type: "number" },
  { key: "targetLeads", group: "Маркетинг", type: "number" },
  { key: "targetLeadRate", group: "Маркетинг", type: "formula" },
  { key: "marketingDrivers", group: "Маркетинг", type: "textarea" },
  { key: "marketingBlockers", group: "Маркетинг", type: "textarea" },
  { key: "responseCost", group: "Маркетинг", type: "formula" },
  { key: "targetLeadCost", group: "Маркетинг", type: "formula" },
  { key: "agreements", group: "Маркетинг", type: "textarea" },
];

const editFields = sourceColumns.filter((c) => !["division", "name", "health"].includes(c.key) && c.type !== "formula");
const moneyMetrics = new Set(["revenueVat", "penalties", "realizationPayroll", "recruitingPayroll", "marketingPayroll", "marketingBudget", "responseCost", "targetLeadCost"]);
const percentMetrics = new Set(["penaltyShare", "requestCloseRate", "invitedToResponseRate", "registeredToInvitedRate", "warehouseToRegisteredRate", "firstShiftToWarehouseRate", "tenShiftsToFirstShiftRate", "responseToWarehouseRate", "targetLeadRate"]);
const textMetrics = new Set(["realizationDrivers", "realizationBlockers", "recruitingDrivers", "recruitingBlockers", "marketingDrivers", "marketingBlockers", "agreements", "marketingProjectName"]);
const groupClassMap = { "Реализация": "realization-zone", "Подбор": "recruiting-zone", "Маркетинг": "marketing-zone" };

const issueFilters = [
  ["all", "Все"],
  ["belowNorm", "Ниже нормы"],
  ["requestClose", "Закрытие < 95%"],
  ["penalty", "Штрафы"],
  ["expensiveLead", "Дорогой целевой"],
  ["noMarketing", "Нет маркетинга при риске"],
  ["noDemand", "Нет потребности"],
  ["lowFunnel", "Просадка воронки"],
];

async function boot() {
  try {
    const [data, norms, actionsResp] = await Promise.all([
      _apiGet("/month/data"),
      _apiGet("/month/norms"),
      _apiGet("/month/actions"),
    ]);
    state.data = data;
    state.norms = norms;
    state.actions = actionsResp.actions || [];
  } catch (err) {
    console.error("Не удалось загрузить с сервера, fallback на seed:", err);
    const [sourceData, norms, actions] = await Promise.all([
      fetch("seed/monthly-reports.json").then((r) => r.json()),
      fetch("seed/norms.json").then((r) => r.json()),
      fetch("seed/actions.json").then((r) => r.json()),
    ]);
    state.data = sourceData;
    state.norms = norms;
    state.actions = actions.actions || [];
  }
  state.monthId = state.data.reports.at(-1).id;
  state.compareMonthId = state.data.reports[0].id;
  recalculateAll();
  applySavedTheme();
  render();
}

function loadSavedData() {
  // legacy stub, оставлен для совместимости
  return null;
}

function saveData() {
  _scheduleSave("/month/data", state.data);
}

function clearSavedData() {
  // На сервере данные не трогаем — просто перезагружаем страницу
  location.reload();
}

function downloadJson() {
  const blob = new Blob([JSON.stringify(state.data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sth-month-data-${state.monthId}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function exportCsv() {
  const rows = selectedProjects().map((p) => {
    const base = [p.division, p.name];
    const values = sourceColumns.slice(3).map((c) => format(valueFor(p, c.key), c.key, true));
    return [...base, ...values].map(csvCell).join(";");
  });
  const header = ["Дивизион", "Проект", ...sourceColumns.slice(3).map((c) => metricLabels[c.key] || c.label || c.key)].map(csvCell).join(";");
  const blob = new Blob([[header, ...rows].join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sth-month-${state.monthId}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function recalculateAll() {
  state.data.reports.forEach((r) => r.projects.forEach(recalculateProject));
}

function recalculateProject(project) {
  const m = project.metrics;
  m.penaltyShare = safeDiv(m.penalties, m.revenueVat);
  m.requestCloseRate = safeDiv(m.avgOutput, m.request);
  m.invitedToResponseRate = safeDiv(m.invited, m.responses);
  m.registeredToInvitedRate = safeDiv(m.registered, m.invited);
  m.warehouseToRegisteredRate = safeDiv(m.warehouseReached, m.registered);
  m.firstShiftToWarehouseRate = safeDiv(m.firstShift, m.warehouseReached);
  m.tenShiftsToFirstShiftRate = safeDiv(m.tenShifts, m.firstShift);
  m.responseToWarehouseRate = safeDiv(m.warehouseReached, m.responses);
  m.targetLeadRate = safeDiv(m.targetLeads, m.responses);
  m.responseCost = safeDiv((m.marketingPayroll || 0) + (m.marketingBudget || 0), m.responses);
  m.targetLeadCost = safeDiv((m.marketingPayroll || 0) + (m.marketingBudget || 0), m.targetLeads);
}

function safeDiv(a, b) {
  return b ? (Number(a) || 0) / b : 0;
}

function report(monthId = state.monthId) {
  return state.data.reports.find((r) => r.id === monthId) || state.data.reports[0];
}

function selectedProjects(monthId = state.monthId) {
  let projects = report(monthId).projects;
  if (state.division !== "all") projects = projects.filter((p) => p.division === state.division);
  if (state.projectId !== "all") projects = projects.filter((p) => p.id === state.projectId);
  if (state.search.trim()) {
    const q = state.search.toLowerCase().trim();
    projects = projects.filter((p) => `${p.name} ${p.division} ${p.metrics.marketingProjectName || ""}`.toLowerCase().includes(q));
  }
  if (state.issueFilter !== "all") projects = projects.filter(matchesIssueFilter);
  return projects;
}

function matchesIssueFilter(project) {
  const m = project.metrics;
  if (state.issueFilter === "belowNorm") return health(project).score < 70 || normViolations(project).length > 0;
  if (state.issueFilter === "requestClose") return (m.requestCloseRate || 0) < 0.95 && (m.request || 0) > 0;
  if (state.issueFilter === "penalty") return (m.penalties || 0) > 0 || (m.penaltyShare || 0) > 0.03;
  if (state.issueFilter === "expensiveLead") return (m.targetLeadCost || 0) > 900 && (m.targetLeads || 0) > 0;
  if (state.issueFilter === "noMarketing") return !m.marketingBudget && !m.responses && (m.requestCloseRate || 0) < 0.95;
  if (state.issueFilter === "noDemand") return !m.request && !m.avgOutput && !m.revenueVat;
  if (state.issueFilter === "lowFunnel") return (m.registeredToInvitedRate || 1) < 0.55 || (m.warehouseToRegisteredRate || 1) < 0.65 || (m.tenShiftsToFirstShiftRate || 1) < 0.45;
  return true;
}

function divisions() {
  return [...new Set(state.data.reports.flatMap((r) => r.projects.map((p) => p.division)))].sort((a, b) => a.localeCompare(b, "ru"));
}

function projectOptions() {
  return [...state.data.projects].sort((a, b) => a.name.localeCompare(b.name, "ru"));
}

function normFor(metric, project) {
  return state.norms.project?.[project?.id]?.[metric] || state.norms.division?.[project?.division]?.[metric] || state.norms.month?.[state.monthId]?.[metric] || state.norms.company?.[metric] || null;
}

function normStatus(value, norm) {
  if (!norm || norm.target === null || value === null || value === undefined || Number.isNaN(value)) return "";
  if (norm.direction === "lte") return value <= norm.target ? "norm-ok" : "norm-bad";
  return value >= norm.target ? "norm-ok" : "norm-bad";
}

function scoreMetric(value, norm) {
  if (!norm || norm.target === null || value === null || value === undefined || Number.isNaN(value)) return null;
  if (norm.direction === "lte") return value <= norm.target ? 100 : Math.max(0, Math.min(100, (norm.target / value) * 100));
  return value >= norm.target ? 100 : Math.max(0, Math.min(100, (value / norm.target) * 100));
}

function normViolations(project) {
  return Object.keys(state.norms.company)
    .map((metric) => ({ metric, value: project.metrics[metric], norm: normFor(metric, project) }))
    .filter((x) => normStatus(x.value, x.norm) === "norm-bad");
}

function health(project) {
  const m = project.metrics;
  if (!m.request && !m.avgOutput && !m.revenueVat) return { score: null, status: "Нет активной потребности", tone: "neutral", details: [] };
  const evaluated = Object.keys(state.norms.company)
    .map((metric) => {
      const norm = normFor(metric, project);
      const raw = scoreMetric(m[metric], norm);
      return raw === null ? null : { metric, raw, weight: norm.weight || 5, value: m[metric], norm };
    })
    .filter(Boolean);
  if (!m.marketingBudget && !m.responses && m.requestCloseRate < 0.95) evaluated.push({ metric: "marketingActivity", raw: 45, weight: 8, value: 0, norm: { target: 1, direction: "gte" } });
  if (!evaluated.length) return { score: null, status: "Недостаточно данных", tone: "neutral", details: [] };
  const totalWeight = evaluated.reduce((s, x) => s + x.weight, 0);
  const score = Math.round(evaluated.reduce((s, x) => s + x.raw * x.weight, 0) / totalWeight);
  const details = evaluated.sort((a, b) => a.raw - b.raw);
  if (score < 50) return { score, status: "Критично", tone: "bad", details };
  if (score < 70) return { score, status: "Зона внимания", tone: "warn", details };
  if (score < 85) return { score, status: "Нормально", tone: "neutral", details };
  return { score, status: "Отлично", tone: "good", details };
}

function healthExplanation(project, limit = 3) {
  const h = health(project);
  if (h.score === null) return "нет активной потребности";
  return h.details.slice(0, limit).map((d) => metricLabels[d.metric] || d.metric).join(", ");
}

function valueFor(project, key) {
  if (key === "division") return project.division;
  if (key === "name") return project.name;
  if (key === "health") return health(project).score;
  return project.metrics[key];
}

function previousProject(project) {
  return report(state.compareMonthId).projects.find((p) => p.id === project.id);
}

function aggregate(projects) {
  const keys = sourceColumns.filter((c) => !textMetrics.has(c.key) && !["division", "name", "health"].includes(c.key)).map((c) => c.key);
  const agg = {};
  keys.forEach((key) => (agg[key] = projects.reduce((s, p) => s + (Number(p.metrics[key]) || 0), 0)));
  agg.penaltyShare = safeDiv(agg.penalties, agg.revenueVat);
  agg.requestCloseRate = safeDiv(agg.avgOutput, agg.request);
  agg.invitedToResponseRate = safeDiv(agg.invited, agg.responses);
  agg.registeredToInvitedRate = safeDiv(agg.registered, agg.invited);
  agg.warehouseToRegisteredRate = safeDiv(agg.warehouseReached, agg.registered);
  agg.firstShiftToWarehouseRate = safeDiv(agg.firstShift, agg.warehouseReached);
  agg.tenShiftsToFirstShiftRate = safeDiv(agg.tenShifts, agg.firstShift);
  agg.responseToWarehouseRate = safeDiv(agg.warehouseReached, agg.responses);
  agg.targetLeadRate = safeDiv(agg.targetLeads, agg.responses);
  agg.responseCost = safeDiv((agg.marketingPayroll || 0) + (agg.marketingBudget || 0), agg.responses);
  agg.targetLeadCost = safeDiv((agg.marketingPayroll || 0) + (agg.marketingBudget || 0), agg.targetLeads);
  agg.secretCheck = average(projects, "secretCheck");
  agg.productivity = average(projects, "productivity");
  return agg;
}

function average(projects, metric) {
  const values = projects.map((p) => p.metrics[metric]).filter((v) => typeof v === "number" && Number.isFinite(v));
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
}

function format(value, metric, raw = false) {
  if (value === null || value === undefined || Number.isNaN(value)) return raw ? "" : "—";
  if (raw) return String(value);
  if (percentMetrics.has(metric)) return `${(value * 100).toFixed(value < 0.1 ? 1 : 0)}%`;
  if (moneyMetrics.has(metric)) {
    if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} млн ₽`;
    return `${Math.round(value).toLocaleString("ru-RU")} ₽`;
  }
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} млн`;
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 1 });
}

function deltaHtml(current, previous, metric) {
  if (previous === null || previous === undefined || Number.isNaN(previous)) return "";
  const diff = (current || 0) - (previous || 0);
  if (!diff) return `<div class="delta-mini muted">без изм.</div>`;
  const cls = diff >= 0 ? "delta-up" : "delta-down";
  return `<div class="delta-mini ${cls}">${diff > 0 ? "+" : ""}${format(diff, metric)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function render() {
  document.querySelector("#app").innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">${sthLogo()}<div><div class="brand-title">STH</div><div class="brand-subtitle">Month dashboard</div></div></div>
        <nav class="nav">${views.map(([id, label]) => `<button class="${state.view === id ? "active" : ""}" data-view="${id}">${label}</button>`).join("")}</nav>
        <button class="theme-toggle" data-theme-toggle title="Переключить тему">${document.documentElement.dataset.theme === "light" ? "🌙 Тёмная тема" : "☀️ Светлая тема"}</button>
        <p class="footer-note">Тестовая версия на GitHub Pages. Таблица-отчет сохранена, изменения пока локальные.</p>
      </aside>
      <main class="main">${topbar()}<section id="screen">${screen()}</section></main>
      ${editDrawer()}
    </div>`;
  bind();
}

function sthLogo() {
  return `<svg class="sth-mark" viewBox="0 0 64 64" aria-label="STH"><g fill="#E62250"><circle cx="14" cy="10" r="8"/><circle cx="40" cy="10" r="8"/><circle cx="56" cy="34" r="8"/><circle cx="14" cy="54" r="8"/><circle cx="40" cy="54" r="8"/><circle cx="56" cy="54" r="8"/><path d="M14 18c0 10 6 16 16 16 7 0 10 4 10 10v10c6 0 10-4 10-10 0-10-6-18-18-18-5 0-8-3-8-8v-8c-6 0-10 4-10 8Z"/></g></svg>`;
}

function topbar() {
  const title = views.find(([id]) => id === state.view)?.[1] || "Дашборд";
  return `
    <div class="topbar">
      <div><h1 class="page-title">${title}</h1><p class="page-note">${pageNote()}</p></div>
      <div class="filters">
        ${selectControl("Месяц", "month", state.monthId, state.data.reports.map((r) => [r.id, r.label]))}
        ${selectControl("Сравнить с", "compare", state.compareMonthId, state.data.reports.map((r) => [r.id, r.label]))}
        ${selectControl("Дивизион", "division", state.division, [["all", "Все дивизионы"], ...divisions().map((d) => [d, d])])}
        ${selectControl("Проект", "project", state.projectId, [["all", "Все проекты"], ...projectOptions().map((p) => [p.id, p.name])])}
      </div>
    </div>`;
}

function pageNote() {
  if (state.view === "dashboard") return "Руководительский экран: отклонения, риски, причины, действия и динамика. Таблица-отчет остается отдельным разделом.";
  if (state.view === "report") return "Исходный отчет в браузере: реализация, подбор, маркетинг, нормы, дельты к месяцу сравнения и быстрые проблемные фильтры.";
  return "Аналитическое представление использует те же данные, что и таблица-отчет.";
}

function selectControl(label, name, value, options) {
  return `<label class="control"><span>${label}</span><select data-control="${name}">${options.map(([id, text]) => `<option value="${escapeHtml(id)}" ${id === value ? "selected" : ""}>${escapeHtml(text)}</option>`).join("")}</select></label>`;
}

function bind() {
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => { state.view = b.dataset.view; render(); }));
  document.querySelectorAll("[data-control]").forEach((c) => c.addEventListener("change", () => {
    if (c.dataset.control === "month") state.monthId = c.value;
    if (c.dataset.control === "compare") state.compareMonthId = c.value;
    if (c.dataset.control === "division") state.division = c.value;
    if (c.dataset.control === "project") state.projectId = c.value;
    render();
  }));
  document.querySelectorAll("[data-open-project]").forEach((b) => b.addEventListener("click", () => { state.projectId = b.dataset.openProject; state.view = "project"; render(); }));
  document.querySelectorAll("[data-edit-project]").forEach((b) => b.addEventListener("click", () => { state.editProjectId = b.dataset.editProject; render(); }));
  document.querySelectorAll("[data-close-drawer]").forEach((b) => b.addEventListener("click", () => { state.editProjectId = null; render(); }));
  document.querySelectorAll("[data-cell]").forEach((input) => input.addEventListener("change", onCellChange));
  document.querySelectorAll("[data-issue-filter]").forEach((b) => b.addEventListener("click", () => { state.issueFilter = b.dataset.issueFilter; render(); }));
  document.querySelectorAll("[data-reset-local]").forEach((b) => b.addEventListener("click", clearSavedData));
  document.querySelectorAll("[data-export-json]").forEach((b) => b.addEventListener("click", downloadJson));
  document.querySelectorAll("[data-export-csv]").forEach((b) => b.addEventListener("click", exportCsv));
  const search = document.querySelector("[data-search]");
  if (search) {
    search.addEventListener("input", (event) => {
      const el = event.target;
      const caret = el.selectionStart;
      state.search = el.value;
      document.querySelector("#screen").innerHTML = screen();
      bind();
      // Восстанавливаем фокус и позицию курсора, иначе после innerHTML фокус
      // улетает на body и каждая буква «обрывает» ввод (#bug 03.06.2026).
      const next = document.querySelector("[data-search]");
      if (next) {
        next.focus();
        try { next.setSelectionRange(caret, caret); } catch (_) {}
      }
    });
  }
  // Переключатель тёмная/светлая тема
  document.querySelectorAll("[data-theme-toggle]").forEach((b) => b.addEventListener("click", toggleTheme));
}

function toggleTheme() {
  const cur = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const next = cur === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("sth-month-theme", next); } catch (_) {}
  render();
}

function applySavedTheme() {
  let saved = "dark";
  try { saved = localStorage.getItem("sth-month-theme") || "dark"; } catch (_) {}
  document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";
}

function onCellChange(event) {
  const input = event.target;
  const project = report(input.dataset.month).projects.find((p) => p.id === input.dataset.project);
  if (!project) return;
  const key = input.dataset.field;
  if (key === "division") project.division = input.value.trim() || project.division;
  else if (key === "name") project.name = input.value.trim() || project.name;
  else if (textMetrics.has(key)) project.metrics[key] = input.value.trim() || null;
  else project.metrics[key] = input.value === "" ? null : Number(input.value);
  recalculateProject(project);
  saveData();
  render();
}

function screen() {
  const screens = { dashboard: dashboardScreen, report: reportScreen, projects: projectsScreen, project: projectScreen, dynamics: dynamicsScreen, funnel: funnelScreen, marketing: marketingScreen, actions: actionsScreen, norms: normsScreen };
  return screens[state.view]();
}

function dashboardScreen() {
  const projects = selectedProjects();
  const current = aggregate(projects);
  const prev = aggregate(filteredReportProjects(state.compareMonthId));
  const healthItems = projects.map((p) => ({ project: p, health: health(p) })).filter((x) => x.health.score !== null);
  const avgHealth = healthItems.length ? Math.round(healthItems.reduce((s, x) => s + x.health.score, 0) / healthItems.length) : null;
  const risky = [...healthItems].sort((a, b) => a.health.score - b.health.score).slice(0, 8);
  const actions = filteredActions();
  return `
    <div class="grid kpi-grid">
      ${kpi("Индекс здоровья", avgHealth, "number", null)}
      ${kpi("Выручка", current.revenueVat, "revenueVat", prev.revenueVat)}
      ${kpi("Закрытие заявки", current.requestCloseRate, "requestCloseRate", prev.requestCloseRate)}
      ${kpi("Стоимость целевого", current.targetLeadCost, "targetLeadCost", prev.targetLeadCost)}
    </div>
    <div class="grid two-col" style="margin-top:14px">
      <div class="card">
        <h2 class="card-title">Карта отклонений</h2>
        <div class="quick-filters">${issueFilters.slice(1).map(([id, label]) => filterPill(id, label, countByIssue(id))).join("")}</div>
        <div class="table-wrap compact-table"><table><thead><tr><th>Проект</th><th>Индекс</th><th>Причины</th><th>Отклонения</th><th></th></tr></thead><tbody>
          ${risky.map(({ project, health: h }) => riskRow(project, h)).join("") || `<tr><td colspan="5">${empty("Нет рисков")}</td></tr>`}
        </tbody></table></div>
      </div>
      <div class="card">
        <h2 class="card-title">Действия к контролю</h2>
        <div class="action-list">${actions.slice(0, 8).map(actionCard).join("") || empty("Нет действий")}</div>
      </div>
    </div>
    <div class="grid three-col" style="margin-top:14px">
      ${summaryCard("Реализация", current, ["request", "avgOutput", "currentStaff", "revenueVat", "penaltyShare", "secretCheck"])}
      ${summaryCard("Подбор", current, ["invited", "registered", "warehouseReached", "firstShift", "tenShifts", "tenShiftsToFirstShiftRate"])}
      ${summaryCard("Маркетинг", current, ["marketingBudget", "responses", "targetLeads", "targetLeadRate", "responseCost", "targetLeadCost"])}
    </div>`;
}

function countByIssue(id) {
  const old = state.issueFilter;
  state.issueFilter = id;
  const count = selectedProjects().length;
  state.issueFilter = old;
  return count;
}

function filterPill(id, label, count) {
  return `<button class="filter-pill ${state.issueFilter === id ? "active" : ""}" data-issue-filter="${id}">${escapeHtml(label)} <strong>${count}</strong></button>`;
}

function riskRow(project, h) {
  const violations = normViolations(project).slice(0, 3).map((v) => metricLabels[v.metric] || v.metric).join(", ");
  return `<tr><td><button class="linkish" data-open-project="${project.id}">${escapeHtml(project.name)}</button><br><span class="muted">${escapeHtml(project.division)}</span></td><td>${badge(h.score ?? "н/д", h.tone)}</td><td>${escapeHtml(healthExplanation(project))}</td><td>${escapeHtml(violations || "без критичных норм")}</td><td><button class="ghost-btn small-btn" data-edit-project="${project.id}">Редактировать</button></td></tr>`;
}

function reportScreen() {
  const projects = selectedProjects();
  const rows = buildGroupedRows(projects);
  return `
    <div class="sheet-toolbar">
      <div><strong>${report().label}</strong><span class="muted"> · ${projects.length} проектов · дельта к ${report(state.compareMonthId).label}</span></div>
      <div class="sheet-actions">
        <input data-search placeholder="Поиск по таблице" value="${escapeHtml(state.search)}">
        <button class="ghost-btn" data-export-csv>CSV</button>
        <button class="ghost-btn" data-export-json>JSON</button>
        <button class="ghost-btn" data-reset-local>Сбросить изменения</button>
      </div>
    </div>
    <div class="quick-filters">${issueFilters.map(([id, label]) => filterPill(id, label, id === "all" ? report().projects.length : countByIssue(id))).join("")}</div>
    <div class="source-table-wrap"><table class="source-table">${sourceHeader()}<tbody>${rows.map(sourceRow).join("")}</tbody></table></div>`;
}

function sourceHeader() {
  const groups = [];
  let current = null;
  sourceColumns.forEach((col) => {
    const name = col.group || "";
    if (!current || current.name !== name) {
      current = { name, count: 0 };
      groups.push(current);
    }
    current.count += 1;
  });
  return `<thead><tr class="group-head">${groups.map((g, i) => `<th colspan="${g.count}" class="${i === 0 ? "corner-head" : groupClass(g.name)}">${escapeHtml(g.name)}</th>`).join("")}</tr><tr>${sourceColumns.map((col) => `<th class="${col.fixed ? "sticky-col head-fixed" : ""} ${groupClass(col.group)}">${escapeHtml(col.label || metricLabels[col.key] || col.key)}</th>`).join("")}</tr></thead>`;
}

function groupClass(group) {
  return groupClassMap[group] || "";
}

function buildGroupedRows(projects) {
  const rows = [];
  let currentDivision = null;
  let buffer = [];
  projects.forEach((project) => {
    if (project.division !== currentDivision) {
      if (buffer.length) rows.push({ type: "total", division: currentDivision, projects: buffer });
      currentDivision = project.division;
      buffer = [];
    }
    buffer.push(project);
    rows.push({ type: "project", project });
  });
  if (buffer.length) rows.push({ type: "total", division: currentDivision, projects: buffer });
  rows.push({ type: "grand", projects });
  return rows;
}

function sourceRow(row) {
  if (row.type !== "project") return totalRow(row);
  return `<tr>${sourceColumns.map((col) => sourceCell(row.project, col)).join("")}</tr>`;
}

function sourceCell(project, col) {
  const value = valueFor(project, col.key);
  const fixed = col.fixed ? "sticky-col" : "";
  const zone = groupClass(col.group);
  const norm = !["division", "name", "health"].includes(col.key) ? normFor(col.key, project) : null;
  const status = normStatus(value, norm);
  const kind = cellKind(col);
  const prev = previousProject(project);
  const prevValue = prev ? valueFor(prev, col.key) : null;
  let main;
  if (col.key === "name") main = `<button class="linkish" data-open-project="${project.id}">${escapeHtml(project.name)}</button><button class="edit-inline" data-edit-project="${project.id}">ред.</button>`;
  else if (col.key === "health") main = badge(value ?? "н/д", health(project).tone);
  else if (kind === "number-cell") main = format(value, col.key);
  else main = escapeHtml(value || "");
  return `<td class="${fixed} ${zone} ${status} ${kind} ${col.type === "formula" ? "formula-cell" : ""}"><div class="cell-main">${main}</div>${normLine(norm, col.key, status, value)}${kind === "number-cell" && col.key !== "health" ? deltaHtml(value, prevValue, col.key) : ""}</td>`;
}

function cellKind(col) {
  if (textMetrics.has(col.key) || col.key === "name" || col.key === "division") return "text-cell";
  return "number-cell";
}

function normLine(norm, key, status, value) {
  if (!norm || norm.target === null || textMetrics.has(key)) return "";
  const sign = norm.direction === "lte" ? "≤" : "≥";
  const diff = (value || 0) - norm.target;
  return `<div class="norm-line ${status}">норма: ${sign} ${format(norm.target, key)} · откл. ${format(diff, key)}</div>`;
}

function totalRow(row) {
  const agg = aggregate(row.projects);
  const label = row.type === "grand" ? `Итоги ${report().label}` : "Итог по дивизиону";
  return `<tr class="total-row"><td class="sticky-col" colspan="3">${escapeHtml(label)}${row.division ? `<br><span>${escapeHtml(row.division)}</span>` : ""}</td>${sourceColumns.slice(3).map((col) => `<td class="${groupClass(col.group)} ${cellKind(col)}">${textMetrics.has(col.key) ? "" : format(agg[col.key], col.key)}</td>`).join("")}</tr>`;
}

function kpi(label, value, metric, previous) {
  return `<div class="card kpi"><div class="kpi-label">${label}</div><div><div class="kpi-value">${format(value, metric)}</div>${previous === null || previous === undefined ? "" : deltaHtml(value, previous, metric)}</div></div>`;
}

function projectsScreen() {
  const projects = selectedProjects().map((p) => ({ ...p, h: health(p) })).sort((a, b) => (a.h.score ?? 999) - (b.h.score ?? 999));
  return `<div class="card"><h2 class="card-title">Реестр проектов <input data-search placeholder="Поиск" value="${escapeHtml(state.search)}"></h2><div class="quick-filters">${issueFilters.map(([id, label]) => filterPill(id, label, id === "all" ? report().projects.length : countByIssue(id))).join("")}</div><div class="table-wrap"><table><thead><tr><th>Проект</th><th>Дивизион</th><th>Индекс</th><th>Почему</th><th>Заявка</th><th>Выход</th><th>Выручка</th><th>Закрытие</th><th>Целевые</th><th></th></tr></thead><tbody>${projects.map((p) => `<tr><td><button class="linkish" data-open-project="${p.id}">${escapeHtml(p.name)}</button></td><td>${escapeHtml(p.division)}</td><td>${badge(p.h.score ?? "н/д", p.h.tone)}</td><td>${escapeHtml(healthExplanation(p))}</td><td>${format(p.metrics.request, "request")}</td><td>${format(p.metrics.avgOutput, "avgOutput")}</td><td>${format(p.metrics.revenueVat, "revenueVat")}</td><td>${format(p.metrics.requestCloseRate, "requestCloseRate")}</td><td>${format(p.metrics.targetLeads, "targetLeads")}</td><td><button class="ghost-btn small-btn" data-edit-project="${p.id}">Редактировать</button></td></tr>`).join("")}</tbody></table></div></div>`;
}

function projectScreen() {
  const project = report().projects.find((p) => p.id === state.projectId) || selectedProjects()[0] || report().projects[0];
  if (!project) return empty("Проект не найден");
  const h = health(project);
  const actions = filteredActions().filter((a) => a.projectId === project.id);
  return `<div class="card project-head"><div><h2 class="project-name">${escapeHtml(project.name)}</h2><p class="muted">${escapeHtml(project.division)}${project.divisionLeader ? `, ${escapeHtml(project.divisionLeader)}` : ""}</p><p>Причины индекса: ${escapeHtml(healthExplanation(project, 5))}</p></div><div>${ring(h.score)}${badge(h.status, h.tone)}<br><button class="primary-btn" data-edit-project="${project.id}">Редактировать</button></div></div><div class="grid three-col" style="margin-top:14px">${summaryCard("Реализация", project.metrics, ["request", "avgOutput", "currentStaff", "revenueVat", "penaltyShare", "requestCloseRate"])}${summaryCard("Подбор", project.metrics, ["invited", "registered", "warehouseReached", "firstShift", "tenShifts", "tenShiftsToFirstShiftRate"])}${summaryCard("Маркетинг", project.metrics, ["marketingBudget", "responses", "targetLeads", "targetLeadRate", "responseCost", "targetLeadCost"])}</div><div class="card" style="margin-top:14px"><h2 class="card-title">История действий</h2><div class="action-list">${actions.map(actionCard).join("") || empty("Нет действий")}</div></div>`;
}

function dynamicsScreen() {
  return `<div class="grid two-col">${["revenueVat", "requestCloseRate", "targetLeads", "targetLeadCost"].map((metric) => `<div class="card"><h2 class="card-title">${metricLabels[metric]}</h2>${lineChart(state.data.reports.map((r) => ({ label: r.label, value: aggregate(filteredReportProjects(r.id))[metric] || 0 })), metric)}</div>`).join("")}</div>`;
}

function filteredReportProjects(monthId) {
  let projects = report(monthId).projects;
  if (state.division !== "all") projects = projects.filter((p) => p.division === state.division);
  if (state.projectId !== "all") projects = projects.filter((p) => p.id === state.projectId);
  return projects;
}

function funnelScreen() {
  const a = aggregate(selectedProjects());
  const steps = [["Отклики", a.responses, 1], ["Приглашенные", a.invited, safeDiv(a.invited, a.responses)], ["Оформленные", a.registered, safeDiv(a.registered, a.invited)], ["Дошли до склада", a.warehouseReached, safeDiv(a.warehouseReached, a.registered)], ["1 смена", a.firstShift, safeDiv(a.firstShift, a.warehouseReached)], ["10 смен", a.tenShifts, safeDiv(a.tenShifts, a.firstShift)]];
  return `<div class="card"><h2 class="card-title">Воронка подбора</h2><div class="funnel">${steps.map(([label, value, rate]) => `<div class="funnel-step"><strong>${label}</strong><div class="bar-track"><div class="bar-fill" style="width:${Math.max(3, Math.min(100, rate * 100))}%"></div></div><span>${format(value, "number")} / ${format(rate, "requestCloseRate")}</span></div>`).join("")}</div></div>`;
}

function marketingScreen() {
  const projects = selectedProjects().filter((p) => p.metrics.responses || p.metrics.marketingBudget || p.metrics.targetLeads);
  const a = aggregate(projects);
  return `<div class="grid kpi-grid">${kpi("Бюджет", a.marketingBudget, "marketingBudget")}${kpi("Отклики", a.responses, "responses")}${kpi("Целевые", a.targetLeads, "targetLeads")}${kpi("Стоимость целевого", a.targetLeadCost, "targetLeadCost")}</div><div class="card" style="margin-top:14px"><h2 class="card-title">Маркетинг по проектам</h2><div class="table-wrap"><table><thead><tr><th>Проект</th><th>Бюджет</th><th>Отклики</th><th>Целевые</th><th>% ЦЛ</th><th>Стоимость целевого</th></tr></thead><tbody>${projects.map((p) => `<tr><td>${escapeHtml(p.name)}</td><td>${format(p.metrics.marketingBudget, "marketingBudget")}</td><td>${format(p.metrics.responses, "responses")}</td><td>${format(p.metrics.targetLeads, "targetLeads")}</td><td>${format(p.metrics.targetLeadRate, "targetLeadRate")}</td><td>${format(p.metrics.targetLeadCost, "targetLeadCost")}</td></tr>`).join("")}</tbody></table></div></div>`;
}

function actionsScreen() {
  const items = filteredActions();
  const cats = [...new Set(items.map((a) => a.category))].sort((a, b) => a.localeCompare(b, "ru"));
  return `<div class="grid kpi-grid">${kpi("Всего действий", items.length, "number")}${kpi("Блокеры", items.filter((a) => a.kind === "blocker").length, "number")}${kpi("Драйверы", items.filter((a) => a.kind === "driver").length, "number")}${kpi("Категорий", cats.length, "number")}</div><div class="card" style="margin-top:14px"><h2 class="card-title">Матрица категорий</h2><div class="quick-filters">${cats.map((c) => `<span class="filter-pill static">${escapeHtml(c)} <strong>${items.filter((a) => a.category === c).length}</strong></span>`).join("")}</div></div><div class="card" style="margin-top:14px"><h2 class="card-title">Проблемы и действия</h2><div class="action-list">${items.map(actionCard).join("") || empty("Нет действий")}</div></div>`;
}

function filteredActions() {
  let items = state.actions.filter((a) => a.monthId === state.monthId);
  if (state.division !== "all") items = items.filter((a) => a.division === state.division);
  if (state.projectId !== "all") items = items.filter((a) => a.projectId === state.projectId);
  return items;
}

function normsScreen() {
  return `<div class="card"><h2 class="card-title">Нормативы и веса индекса</h2><div class="table-wrap"><table><thead><tr><th>Метрика</th><th>Цель</th><th>Логика</th><th>Вес</th><th>Где применяется</th></tr></thead><tbody>${Object.entries(state.norms.company).map(([metric, norm]) => `<tr><td>${metricLabels[metric] || metric}</td><td>${norm.target === null ? "задается на месяц/проект" : format(norm.target, metric)}</td><td>${norm.direction === "lte" ? "меньше или равно" : "больше или равно"}</td><td>${norm.weight}</td><td>проект → дивизион → месяц → компания</td></tr>`).join("")}</tbody></table></div></div>`;
}

function editDrawer() {
  if (!state.editProjectId) return "";
  const project = report().projects.find((p) => p.id === state.editProjectId);
  if (!project) return "";
  return `<div class="drawer-backdrop"><aside class="edit-drawer"><div class="drawer-head"><div><h2>${escapeHtml(project.name)}</h2><p class="muted">${escapeHtml(project.division)} · ${report().label}</p></div><button class="ghost-btn" data-close-drawer>Закрыть</button></div><div class="drawer-fields">${drawerIdentity(project)}${drawerGroup(project, "Реализация")}${drawerGroup(project, "Подбор")}${drawerGroup(project, "Маркетинг")}</div></aside></div>`;
}

function drawerIdentity(project) {
  return `<section><h3>Проект</h3><label>Дивизион<input data-cell data-month="${project.monthId}" data-project="${project.id}" data-field="division" value="${escapeHtml(project.division)}"></label><label>Название<input data-cell data-month="${project.monthId}" data-project="${project.id}" data-field="name" value="${escapeHtml(project.name)}"></label></section>`;
}

function drawerGroup(project, group) {
  const fields = editFields.filter((f) => f.group === group);
  return `<section><h3>${group}</h3>${fields.map((field) => drawerField(project, field)).join("")}</section>`;
}

function drawerField(project, field) {
  const value = valueFor(project, field.key);
  const attr = `data-cell data-month="${project.monthId}" data-project="${project.id}" data-field="${field.key}"`;
  const control = field.type === "textarea" ? `<textarea ${attr}>${escapeHtml(value || "")}</textarea>` : `<input ${attr} type="${field.type === "number" ? "number" : "text"}" value="${escapeHtml(format(value, field.key, true))}">`;
  const norm = normFor(field.key, project);
  return `<label>${escapeHtml(metricLabels[field.key] || field.key)}${control}${normLine(norm, field.key, normStatus(value, norm), value)}</label>`;
}

function summaryCard(title, metrics, keys) {
  return `<div class="card"><h2 class="card-title">${title}</h2><div class="metric-list">${keys.map((key) => metricLine(metricLabels[key] || key, format(metrics[key], key))).join("")}</div></div>`;
}

function metricLine(label, value) {
  return `<div class="metric-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function actionCard(action) {
  return `<div class="action"><div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap"><strong>${escapeHtml(action.projectName)} · ${escapeHtml(action.department)}</strong>${badge(action.status, action.kind === "blocker" ? "bad" : action.kind === "driver" ? "good" : "neutral")}</div><div class="muted">${escapeHtml(action.title)} · ${escapeHtml(action.division)} · ${escapeHtml(report(action.monthId).label)} · ${escapeHtml(action.category)}</div><div>${escapeHtml(action.text)}</div><div class="muted">Ответственный: ${escapeHtml(action.owner || "не указан")} · срок: ${escapeHtml(action.dueDate || "не задан")}</div></div>`;
}

function badge(text, tone = "neutral") {
  return `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
}

function ring(score) {
  const safe = score ?? 0;
  return `<svg class="health-ring" viewBox="0 0 42 42"><circle cx="21" cy="21" r="15.9" fill="transparent" stroke="#303030" stroke-width="4"></circle><circle cx="21" cy="21" r="15.9" fill="transparent" stroke="#E62250" stroke-width="4" stroke-dasharray="${safe} ${100 - safe}" stroke-dashoffset="25" transform="rotate(-90 21 21)"></circle><text x="21" y="23.5" text-anchor="middle" fill="#fff" font-size="9" font-weight="700">${score ?? "—"}</text></svg>`;
}

function lineChart(points, metric) {
  if (!points.length) return empty("Нет данных");
  const width = 640, height = 230, pad = 32;
  const values = points.map((p) => p.value || 0);
  const min = Math.min(...values, 0), max = Math.max(...values, 1);
  const x = (i) => (points.length === 1 ? width / 2 : pad + (i * (width - pad * 2)) / (points.length - 1));
  const y = (v) => height - pad - ((v - min) / (max - min || 1)) * (height - pad * 2);
  const d = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.value || 0)}`).join(" ");
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><path d="M ${pad} ${height - pad} H ${width - pad}" stroke="#343434" /><path d="${d}" fill="none" stroke="#E62250" stroke-width="4" vector-effect="non-scaling-stroke" />${points.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.value || 0)}" r="5" fill="#fff" />`).join("")}${points.map((p, i) => `<text x="${x(i)}" y="${height - 8}" text-anchor="middle" fill="#a8a8a8" font-size="13">${escapeHtml(p.label.replace(" 2026", ""))}</text>`).join("")}${points.map((p, i) => `<text x="${x(i)}" y="${Math.max(18, y(p.value || 0) - 12)}" text-anchor="middle" fill="#fff" font-size="13">${format(p.value || 0, metric)}</text>`).join("")}</svg>`;
}

function empty(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

boot().catch((error) => {
  document.querySelector("#app").innerHTML = `<div class="boot">Ошибка загрузки: ${escapeHtml(error.message)}</div>`;
});
