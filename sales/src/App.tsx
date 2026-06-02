import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, BarChart3, Database, Filter, GitBranch, Link2, Moon, RefreshCw, Sun, Upload } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { db } from "./db";
import { importCsv } from "./importer";
import { ensureDefaultStageMapping } from "./stageDefaults";
import { entitySummary, getFiltered, groupCount, kpis, options } from "./analytics";
import { FIELD, amountOf, parseBitrixDate, responsibleOf, sourceOf, stageOf, titleOf, value } from "./fields";
import { classifyRecord, cumulativeLeadFunnel, dealOnlyFunnel, funnelDistribution, leadOnlyFunnel, lossReasonFor, stageTitle } from "./funnel";
import type { DashboardFilters, EntityLink, EntityType, StageMapping, StoredEntity } from "./types";

type MainTab = "leadFunnel" | "dealFunnel" | "workingClients" | "through";
type Breakdown = "funnel" | "source" | "manager" | "project";

// STH brand palette: акцент + поддерживающие тона (без кричащих синих/зелёных)
const COLORS = ["#E62250", "#FF7A99", "#FFD166", "#3DDC97", "#6AA5FF", "#B07BFF", "#A8A8A8"];
const BREAKDOWN_LABEL: Record<Breakdown, string> = {
  funnel: "Воронка",
  source: "Источники",
  manager: "Менеджеры",
  project: "Проекты",
};

const initialFilters: DashboardFilters = {
  periodFrom: "",
  periodTo: "",
  responsible: "",
  source: "",
  project: "",
  stage: "",
  linkState: "all",
};

type Theme = "dark" | "light";

function App() {
  const [active, setActive] = useState<MainTab>("leadFunnel");
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === "undefined") return "dark";
    const saved = window.localStorage.getItem("sth_sales_theme");
    return saved === "light" ? "light" : "dark";
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { window.localStorage.setItem("sth_sales_theme", theme); } catch { /* ignore */ }
  }, [theme]);
  const [leadBreakdown, setLeadBreakdown] = useState<Breakdown>("funnel");
  const [dealBreakdown, setDealBreakdown] = useState<Breakdown>("funnel");
  const [leads, setLeads] = useState<StoredEntity[]>([]);
  const [deals, setDeals] = useState<StoredEntity[]>([]);
  const [links, setLinks] = useState<EntityLink[]>([]);
  const [mapping, setMapping] = useState<StageMapping[]>([]);
  const [filters, setFilters] = useState(initialFilters);
  const [selected, setSelected] = useState<StoredEntity | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  const data = useMemo(() => ({ leads, deals, links, mapping }), [leads, deals, links, mapping]);
  const filtered = useMemo(() => getFiltered(data, filters), [data, filters]);
  const stats = useMemo(() => kpis(data, filters), [data, filters]);
  const filterOptions = useMemo(() => options(data), [data]);
  const linkMap = useMemo(() => new Map(links.map((link) => [link.dealId, link])), [links]);

  async function refresh() {
    await ensureDefaultStageMapping();
    const [nextLeads, nextDeals, nextLinks, nextMapping] = await Promise.all([
      db.leads.toArray(),
      db.deals.toArray(),
      db.entityLinks.toArray(),
      db.stageMapping.toArray(),
    ]);
    setLeads(nextLeads);
    setDeals(nextDeals);
    setLinks(nextLinks);
    setMapping(nextMapping);
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleImport(type: EntityType, file?: File) {
    if (!file) return;
    setBusy(`Загружаю ${type === "lead" ? "лиды" : "сделки"}...`);
    try {
      const result = await importCsv(type, file);
      setNotice(`Загружено ${result.importRun.rowCount}. Новых: ${result.importRun.inserted}, обновлено: ${result.importRun.updated}.`);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить файл.");
    } finally {
      setBusy("");
    }
  }

  async function handleImportFromData(type: EntityType) {
    const path = type === "lead" ? "./data/leads.csv" : "./data/deals.csv";
    setBusy(`Загружаю ${type === "lead" ? "лиды" : "сделки"} из data...`);
    try {
      const response = await fetch(`${path}?t=${Date.now()}`);
      if (!response.ok) throw new Error(`Файл ${path} не найден.`);
      const blob = await response.blob();
      const file = new File([blob], type === "lead" ? "leads.csv" : "deals.csv", { type: "text/csv" });
      await handleImport(type, file);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить файл из data.");
      setBusy("");
    }
  }

  const leadStages = leadOnlyFunnel(filtered.leads);
  const dealStages = dealOnlyFunnel(filtered.deals);
  const leadLossData = groupCount(
    filtered.leads.filter((lead) => classifyRecord("lead", lead.raw).kind === "loss"),
    (lead) => lossReasonFor("lead", stageOf("lead", lead.raw)),
  );
  const dealLossData = groupCount(
    filtered.deals.filter((deal) => classifyRecord("deal", deal.raw).kind === "loss"),
    (deal) => lossReasonFor("deal", stageOf("deal", deal.raw)),
  );
  const linkData = groupCount(links, (link) => link.confidence);
  const throughSteps = cumulativeLeadFunnel(
    filtered.leads,
    filtered.deals.filter((deal) => classifyRecord("deal", deal.raw).kind !== "service").length,
    filtered.deals.filter((deal) => classifyRecord("deal", deal.raw).kind === "success").length,
  );

  return (
    <div className="app shell">
      <aside className="sidebar compact">
        <div className="brand">
          <BarChart3 size={26} />
          <div>
            <strong>Продажи</strong>
            <span>Bitrix dashboard</span>
          </div>
        </div>
        <nav>
          <button className={active === "leadFunnel" ? "active" : ""} onClick={() => setActive("leadFunnel")}>
            <BarChart3 size={18} /> Воронка по лидам
          </button>
          <button className={active === "dealFunnel" ? "active" : ""} onClick={() => setActive("dealFunnel")}>
            <BarChart3 size={18} /> Воронка по сделкам
          </button>
          <button className={active === "workingClients" ? "active" : ""} onClick={() => setActive("workingClients")}>
            <Database size={18} /> Клиенты в работе
          </button>
          <button className={active === "through" ? "active" : ""} onClick={() => setActive("through")}>
            <GitBranch size={18} /> Сквозная
          </button>
        </nav>
        <div className="sidebar-foot">
          <button
            className="theme-toggle"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему"}
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            {theme === "dark" ? "Светлая тема" : "Тёмная тема"}
          </button>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <h1>
              {active === "leadFunnel"
                ? "Воронка по лидам"
                : active === "dealFunnel"
                  ? "Воронка по сделкам"
                  : active === "workingClients"
                    ? "Клиенты в работе"
                    : "Сквозная аналитика"}
            </h1>
            <p>Период, источники, менеджеры и проекты меняются общими фильтрами.</p>
          </div>
          <div className="upload-row">
            <label className="icon-button">
              <Upload size={18} /> Лиды CSV
              <input type="file" accept=".csv,text/csv" onChange={(e) => handleImport("lead", e.target.files?.[0])} />
            </label>
            <label className="icon-button">
              <Upload size={18} /> Сделки CSV
              <input type="file" accept=".csv,text/csv" onChange={(e) => handleImport("deal", e.target.files?.[0])} />
            </label>
            <button className="icon-button" onClick={() => handleImportFromData("lead")}>
              <Database size={18} /> Лиды из data
            </button>
            <button className="icon-button" onClick={() => handleImportFromData("deal")}>
              <Database size={18} /> Сделки из data
            </button>
          </div>
        </header>

        {(notice || busy) && (
          <div className="notice">
            {busy && <RefreshCw className="spin" size={16} />}
            {busy || notice}
          </div>
        )}

        <Filters filters={filters} setFilters={setFilters} selectOptions={filterOptions} />

        {active === "leadFunnel" && (
          <FunnelTab
            type="lead"
            rows={filtered.leads}
            stages={leadStages}
            breakdown={leadBreakdown}
            setBreakdown={setLeadBreakdown}
            mapping={mapping}
            onSelect={setSelected}
            extra={<LossPanel title="Почему теряются лиды" type="lead" rows={filtered.leads} lossData={leadLossData} onSelect={setSelected} />}
          />
        )}

        {active === "dealFunnel" && (
          <FunnelTab
            type="deal"
            rows={filtered.deals}
            stages={dealStages}
            breakdown={dealBreakdown}
            setBreakdown={setDealBreakdown}
            mapping={mapping}
            onSelect={setSelected}
            extra={<LossPanel title="Почему теряются сделки" type="deal" rows={filtered.deals} lossData={dealLossData} onSelect={setSelected} />}
          />
        )}

        {active === "workingClients" && (
          <WorkingClientsTab deals={filtered.deals} onSelect={setSelected} />
        )}

        {active === "through" && (
          <ThroughTab
            stats={stats}
            steps={throughSteps}
            leadLossData={leadLossData}
            dealLossData={dealLossData}
            linkData={linkData}
            deals={filtered.deals}
            leads={leads}
            linkMap={linkMap}
            onSelect={setSelected}
          />
        )}
      </main>

      {selected && <EntityDrawer entity={selected} links={links} leads={leads} deals={deals} onClose={() => setSelected(null)} />}
    </div>
  );
}

function Filters({
  filters,
  setFilters,
  selectOptions,
}: {
  filters: DashboardFilters;
  setFilters: (filters: DashboardFilters) => void;
  selectOptions: ReturnType<typeof options>;
}) {
  const set = (key: keyof DashboardFilters, value: string) => setFilters({ ...filters, [key]: value });
  return (
    <section className="filters report-filters">
      <Filter size={18} />
      <label>
        <span>От</span>
        <input type="date" value={filters.periodFrom} onChange={(e) => set("periodFrom", e.target.value)} />
      </label>
      <label>
        <span>До</span>
        <input type="date" value={filters.periodTo} onChange={(e) => set("periodTo", e.target.value)} />
      </label>
      <Select value={filters.source} onChange={(value) => set("source", value)} options={selectOptions.source} label="Источник" />
      <Select value={filters.responsible} onChange={(value) => set("responsible", value)} options={selectOptions.responsible} label="Менеджер" />
      <Select value={filters.project} onChange={(value) => set("project", value)} options={selectOptions.project} label="Проект" />
      <Select value={filters.stage} onChange={(value) => set("stage", value)} options={selectOptions.stage} label="Стадия" />
      <button onClick={() => setFilters(initialFilters)}>Сброс</button>
    </section>
  );
}

function Select({ value, onChange, options, label }: { value: string; onChange: (value: string) => void; options: string[]; label: string }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{label}</option>
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

function FunnelTab({
  type,
  rows,
  stages,
  breakdown,
  setBreakdown,
  mapping,
  onSelect,
  extra,
}: {
  type: EntityType;
  rows: StoredEntity[];
  stages: Array<{ name?: string; label?: string; value: number; kind: string; sourceStages?: string[] }>;
  breakdown: Breakdown;
  setBreakdown: (value: Breakdown) => void;
  mapping: StageMapping[];
  onSelect: (entity: StoredEntity) => void;
  extra?: ReactNode;
}) {
  const groups = breakdownRows(rows, type, breakdown, mapping);
  const chartGroups = groups.map((item) => ({ name: "label" in item ? item.label : item.name, value: item.value }));
  const totalAmount = rows.reduce((sum, item) => sum + amountOf(item.raw), 0);
  const activeCount = rows.filter((row) => classifyRecord(type, row.raw).kind === "active").length;
  const successCount = rows.filter((row) => classifyRecord(type, row.raw).kind === "success").length;
  const lossCount = rows.filter((row) => classifyRecord(type, row.raw).kind === "loss").length;
  return (
    <>
      <Segmented value={breakdown} onChange={setBreakdown} />
      <section className="kpi-grid compact-kpis">
        <Kpi title={type === "lead" ? "Всего лидов" : "Всего сделок"} value={rows.length} />
        <Kpi title="В работе" value={activeCount} />
        <Kpi title={type === "lead" ? "Целевые" : "Успешно"} value={successCount} />
        <Kpi title="Потери" value={lossCount} />
        <Kpi title="Сумма" value={formatMoney(totalAmount)} />
      </section>
      <section className="grid two">
        <Panel title={breakdown === "funnel" ? "Воронка" : BREAKDOWN_LABEL[breakdown]}>
          {breakdown === "funnel" ? <FunnelBars data={stages} rows={rows} type={type} onSelect={onSelect} /> : <HorizontalBars data={chartGroups.slice(0, 14)} />}
        </Panel>
        <Panel title="Таблица среза">
          <PivotTable rows={rows} type={type} groupBy={breakdown} mapping={mapping} />
        </Panel>
        {extra}
        <Panel title={type === "lead" ? "Лиды" : "Сделки"} wide>
          <EntityTable rows={rows.slice(0, 120)} onSelect={onSelect} />
        </Panel>
      </section>
    </>
  );
}

function ThroughTab({
  stats,
  steps,
  leadLossData,
  dealLossData,
  linkData,
  deals,
  leads,
  linkMap,
  onSelect,
}: {
  stats: ReturnType<typeof kpis>;
  steps: Array<{ name: string; value: number }>;
  leadLossData: Array<{ name: string; value: number }>;
  dealLossData: Array<{ name: string; value: number }>;
  linkData: Array<{ name: string; value: number }>;
  deals: StoredEntity[];
  leads: StoredEntity[];
  linkMap: Map<string, EntityLink>;
  onSelect: (entity: StoredEntity) => void;
}) {
  return (
    <>
      <section className="kpi-grid compact-kpis">
        <Kpi title="Лиды" value={stats.leads} />
        <Kpi title="Сделки" value={stats.deals} />
        <Kpi title="Уверенные связи" value={stats.strongLinks} hint={`${stats.linkRate}% сделок`} />
        <Kpi title="Конверсия" value={`${stats.conversion}%`} />
        <Kpi title="Сумма сделок" value={formatMoney(stats.amount)} />
      </section>
      <section className="grid two">
        <Panel title="Общая воронка продаж" wide>
          <SalesFunnel steps={steps} />
        </Panel>
        <LossPanel title="Потери по лидам" type="lead" rows={leads} lossData={leadLossData} onSelect={onSelect} />
        <LossPanel title="Потери по сделкам" type="deal" rows={deals} lossData={dealLossData} onSelect={onSelect} />
        <Panel title="Качество связки">
          <ResponsiveContainer width="100%" height={290}>
            <PieChart>
              <Pie data={linkData} dataKey="value" nameKey="name" innerRadius={60} outerRadius={104}>
                {linkData.map((_, index) => (
                  <Cell key={index} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </Panel>
        <div className="panel warning-panel">
          <AlertTriangle size={24} />
          <h2>Сквозная аналитика</h2>
          <p>В основной конверсии учитываются только уверенные связи по телефону, email, DKT или roistat.</p>
          <div className="link-stats">
            {linkData.map((item) => (
              <span key={item.name}>
                {item.name}: <b>{item.value}</b>
              </span>
            ))}
          </div>
        </div>
        <Panel title="Связанные сделки" wide>
          <LinkedTable deals={deals} linkMap={linkMap} leads={leads} onSelect={onSelect} />
        </Panel>
      </section>
    </>
  );
}

function WorkingClientsTab({ deals, onSelect }: { deals: StoredEntity[]; onSelect: (entity: StoredEntity) => void }) {
  const [stageFilter, setStageFilter] = useState("all");
  const stageLabels = {
    all: "Все",
    agreement: "Согласование",
    proposal: "КП",
    work: "Проработка",
    qualification: "Квалификация",
  } as Record<string, string>;

  const activeDeals = deals
    .filter(isLiveDeal)
    .sort((a, b) => {
      const stageDiff = liveStageOrder(a) - liveStageOrder(b);
      if (stageDiff !== 0) return stageDiff;
      return (lastContactDate(a)?.getTime() || 0) - (lastContactDate(b)?.getTime() || 0);
    });
  const visibleDeals = activeDeals.filter((deal) => stageFilter === "all" || classifyRecord("deal", deal.raw).key === stageFilter);
  const staleDeals = activeDeals.filter((deal) => daysWithoutContact(deal) >= 7);
  const byStage = groupCount(activeDeals, (deal) => stageTitle("deal", deal.raw));
  return (
    <>
      <section className="kpi-grid compact-kpis">
        <Kpi title="Клиентов в работе" value={activeDeals.length} />
        <Kpi title="КП / предложение" value={activeDeals.filter((deal) => classifyRecord("deal", deal.raw).key === "proposal").length} />
        <Kpi title="Согласование" value={activeDeals.filter((deal) => classifyRecord("deal", deal.raw).key === "agreement").length} />
        <Kpi title="Нет контакта 7+ дней" value={staleDeals.length} />
        <Kpi title="Сумма в работе" value={formatMoney(activeDeals.reduce((sum, deal) => sum + amountOf(deal.raw), 0))} />
      </section>
      <section className="grid two">
        <Panel title="Где находятся клиенты">
          <HorizontalBars data={byStage} />
        </Panel>
        <div className="panel warning-panel">
          <h2>Для совещания по продажам</h2>
          <p>
            Здесь только активные сделки, где еще нет договора: квалификация, проработка, КП и согласование. Нецелевые,
            проигранные, технические и уже успешные сделки сюда не попадают.
          </p>
        </div>
        <ManagerQualityPanel deals={deals} />
        <StuckDealsPanel deals={activeDeals} onSelect={onSelect} />
        <Panel title="Список клиентов в работе" wide>
          <div className="stage-filter">
            {Object.entries(stageLabels).map(([key, label]) => (
              <button key={key} className={stageFilter === key ? "active" : ""} onClick={() => setStageFilter(key)}>
                {label}
                <span>{key === "all" ? activeDeals.length : activeDeals.filter((deal) => classifyRecord("deal", deal.raw).key === key).length}</span>
              </button>
            ))}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Клиент</th>
                  <th>Этап</th>
                  <th>Стадия Bitrix</th>
                  <th>Менеджер</th>
                  <th>Сумма</th>
                  <th>Последний контакт</th>
                  <th>Без контакта</th>
                  <th>Комментарий</th>
                </tr>
              </thead>
              <tbody>
                {visibleDeals.map((deal) => {
                  const days = daysWithoutContact(deal);
                  return (
                    <tr key={deal.id} onClick={() => onSelect(deal)} className={days >= 7 ? "stale-row" : ""}>
                      <td><BitrixLink type="deal" id={deal.id} /></td>
                      <td>{titleOf("deal", deal.raw)}</td>
                      <td>{stageTitle("deal", deal.raw)}</td>
                      <td>{stageOf("deal", deal.raw)}</td>
                      <td>{responsibleOf(deal.raw)}</td>
                      <td>{formatMoney(amountOf(deal.raw))}</td>
                      <td>{lastContactText(deal)}</td>
                      <td>{Number.isFinite(days) ? `${days} дн.` : "нет даты"}</td>
                      <td>{commentOf(deal) || "Комментарий не заполнен"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      </section>
    </>
  );
}

function ManagerQualityPanel({ deals }: { deals: StoredEntity[] }) {
  const byManager = new Map<string, StoredEntity[]>();
  deals
    .filter((deal) => classifyRecord("deal", deal.raw).kind !== "service")
    .forEach((deal) => {
      const manager = responsibleOf(deal.raw);
      byManager.set(manager, [...(byManager.get(manager) || []), deal]);
    });
  const rows = [...byManager.entries()]
    .map(([manager, managerDeals]) => {
      const active = managerDeals.filter(isLiveDeal);
      const success = managerDeals.filter((deal) => classifyRecord("deal", deal.raw).kind === "success");
      const loss = managerDeals.filter((deal) => classifyRecord("deal", deal.raw).kind === "loss");
      const stale = active.filter((deal) => daysWithoutContact(deal) >= 7);
      return {
        manager,
        total: managerDeals.length,
        active: active.length,
        success: success.length,
        loss: loss.length,
        stale: stale.length,
        conversion: managerDeals.length ? Math.round((success.length / managerDeals.length) * 1000) / 10 : 0,
      };
    })
    .filter((row) => row.total > 0)
    .sort((a, b) => b.active - a.active || b.success - a.success || b.total - a.total);

  return (
    <Panel title="Качество работы менеджеров" wide>
      <div className="table-wrap compact-table">
        <table>
          <thead>
            <tr>
              <th>Менеджер</th>
              <th>Всего сделок</th>
              <th>В работе</th>
              <th>Успешно</th>
              <th>Потери</th>
              <th>Конверсия в договор</th>
              <th>Нет контакта 7+ дней</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.manager}>
                <td><b>{row.manager}</b></td>
                <td>{row.total}</td>
                <td>{row.active}</td>
                <td>{row.success}</td>
                <td>{row.loss}</td>
                <td>{row.conversion}%</td>
                <td className={row.stale ? "risk-cell" : ""}>{row.stale || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function StuckDealsPanel({ deals, onSelect }: { deals: StoredEntity[]; onSelect: (entity: StoredEntity) => void }) {
  const stuckDeals = deals
    .map((deal) => ({ deal, days: daysOnStage(deal), limit: stuckLimit(deal) }))
    .filter(({ days, limit }) => days >= limit)
    .sort((a, b) => b.days - a.days)
    .slice(0, 80);

  return (
    <Panel title="Застрявшие сделки" wide>
      <p className="muted">Сюда попадают активные сделки, которые слишком долго стоят на текущем этапе: квалификация 7+ дней, проработка 10+ дней, КП 7+ дней, согласование 10+ дней.</p>
      <div className="table-wrap compact-table">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Клиент</th>
              <th>Этап</th>
              <th>Менеджер</th>
              <th>Дней на этапе</th>
              <th>Последний контакт</th>
              <th>Комментарий</th>
            </tr>
          </thead>
          <tbody>
            {stuckDeals.map(({ deal, days }) => (
              <tr key={deal.id} onClick={() => onSelect(deal)} className="stale-row">
                <td><BitrixLink type="deal" id={deal.id} /></td>
                <td>{titleOf("deal", deal.raw)}</td>
                <td>{stageTitle("deal", deal.raw)}</td>
                <td>{responsibleOf(deal.raw)}</td>
                <td>{days} дн.</td>
                <td>{lastContactText(deal)}</td>
                <td>{commentOf(deal) || "Комментарий не заполнен"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Segmented({ value, onChange }: { value: Breakdown; onChange: (value: Breakdown) => void }) {
  return (
    <div className="segment-tabs">
      {(Object.keys(BREAKDOWN_LABEL) as Breakdown[]).map((key) => (
        <button key={key} className={value === key ? "active" : ""} onClick={() => onChange(key)}>
          {BREAKDOWN_LABEL[key]}
        </button>
      ))}
    </div>
  );
}

function Kpi({ title, value, hint }: { title: string; value: number | string; hint?: string }) {
  return (
    <div className="kpi">
      <span>{title}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </div>
  );
}

function Panel({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`panel ${wide ? "wide" : ""}`}>
      <h2>{title}</h2>
      {children}
    </div>
  );
}

function FunnelBars({
  data,
  rows,
  type,
  onSelect,
}: {
  data: Array<{ key?: string; name?: string; label?: string; value: number; kind: string; sourceStages?: string[] }>;
  rows: StoredEntity[];
  type: EntityType;
  onSelect: (entity: StoredEntity) => void;
}) {
  const [selectedStep, setSelectedStep] = useState(data[0]?.key || "");
  const max = Math.max(...data.map((item) => item.value), 1);
  const activeStep = data.find((item) => item.key === selectedStep) || data[0];
  const stageRows = activeStep
    ? rows.filter((row) => stageTitle(type, row.raw) === activeStep.label || classifyRecord(type, row.raw).key === activeStep.key)
    : [];
  return (
    <>
      <div className="funnel-list">
        {data.map((item, index) => {
          const fromStart = max ? Math.round((item.value / max) * 1000) / 10 : 0;
          const visualWidth = Math.max(100 - index * 11, 34);
          const tooltip = item.sourceStages?.length ? `Включены стадии Bitrix:\n${item.sourceStages.join("\n")}` : "Стадии пока не найдены";
          return (
            <div
              className={`funnel-row ${item.kind} ${activeStep?.key === item.key ? "selected" : ""}`}
              key={item.label || item.name}
              title={tooltip}
              onClick={() => setSelectedStep(item.key || "")}
            >
              <div>
                <b>{index + 1}. {item.label || item.name}</b>
                <span>
                  {item.value} · {index === 0 ? "100%" : `${fromStart}% от входа`}
                </span>
              </div>
              <i style={{ width: `${visualWidth}%` }}>
                <em />
              </i>
              <small>{tooltip}</small>
            </div>
          );
        })}
      </div>
      {activeStep && (
        <div className="stage-drilldown">
          <h3>{activeStep.label || activeStep.name}: кто внутри этапа</h3>
          <div className="table-wrap compact-table">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Клиент / название</th>
                  <th>Стадия Bitrix</th>
                  <th>Комментарий</th>
                </tr>
              </thead>
              <tbody>
                {stageRows.slice(0, 80).map((row) => (
                  <tr key={`${row.type}-${row.id}`} onClick={() => onSelect(row)}>
                    <td><BitrixLink type={row.type} id={row.id} /></td>
                    <td>{titleOf(row.type, row.raw)}</td>
                    <td>{stageOf(row.type, row.raw)}</td>
                    <td>{commentOf(row) || "Комментарий не заполнен"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function SalesFunnel({ steps }: { steps: Array<{ name: string; value: number; sourceStages?: string[] }> }) {
  const first = steps[0]?.value || 0;
  return (
    <div className="sales-funnel">
      {steps.map((step, index) => {
        const prev = steps[index - 1]?.value || step.value;
        const fromStart = first ? Math.round((step.value / first) * 1000) / 10 : 0;
        const fromPrev = prev ? Math.round((step.value / prev) * 1000) / 10 : 0;
        return (
          <div key={step.name} title={step.sourceStages?.length ? `Включены стадии Bitrix:\n${step.sourceStages.join("\n")}` : ""}>
            <strong>{step.value}</strong>
            <span>{step.name}</span>
            <small>{index === 0 ? "100%" : `${fromPrev}% от шага / ${fromStart}% от входа`}</small>
            <i style={{ width: `${Math.max(100 - index * 11, 40)}%` }} />
          </div>
        );
      })}
    </div>
  );
}

function HorizontalBars({ data }: { data: Array<{ name: string; value: number }> }) {
  return (
    <ResponsiveContainer width="100%" height={330}>
      <BarChart data={data} layout="vertical" margin={{ left: 18, right: 20 }}>
        <CartesianGrid strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" allowDecimals={false} />
        <YAxis dataKey="name" type="category" width={210} tick={{ fontSize: 12 }} />
        <Tooltip />
        <Bar dataKey="value" fill="#1f4ed8" radius={[0, 5, 5, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function LossPanel({
  title,
  type,
  rows,
  lossData,
  onSelect,
}: {
  title: string;
  type: EntityType;
  rows: StoredEntity[];
  lossData: Array<{ name: string; value: number }>;
  onSelect: (entity: StoredEntity) => void;
}) {
  const [selectedReason, setSelectedReason] = useState("");
  const [lossGroup, setLossGroup] = useState("all");
  const lostRows = rows.filter((row) => classifyRecord(type, row.raw).kind === "loss");
  const visibleLostRows = lostRows.filter((row) => lossGroup === "all" || lossGroupFor(type, stageOf(type, row.raw)) === lossGroup);
  const visibleLossData = groupCount(visibleLostRows, (row) => lossReasonFor(type, stageOf(type, row.raw)));
  const total = visibleLossData.reduce((sum, item) => sum + item.value, 0);
  const activeReason = selectedReason && visibleLossData.some((item) => item.name === selectedReason) ? selectedReason : visibleLossData[0]?.name || "";
  const lossGroups = [
    { key: "all", label: "Все", value: lostRows.length },
    { key: "client", label: "Отказ клиента", value: lostRows.filter((row) => lossGroupFor(type, stageOf(type, row.raw)) === "client").length },
    { key: "our", label: "Наш отказ", value: lostRows.filter((row) => lossGroupFor(type, stageOf(type, row.raw)) === "our").length },
    { key: "notTarget", label: "Нецелевые", value: lostRows.filter((row) => lossGroupFor(type, stageOf(type, row.raw)) === "notTarget").length },
    { key: "other", label: "Прочее", value: lostRows.filter((row) => lossGroupFor(type, stageOf(type, row.raw)) === "other").length },
  ].filter((item) => item.value > 0 || item.key === "all");
  const comments = rows
    .filter((row) => classifyRecord(type, row.raw).kind === "loss")
    .filter((row) => lossGroup === "all" || lossGroupFor(type, stageOf(type, row.raw)) === lossGroup)
    .filter((row) => lossReasonFor(type, stageOf(type, row.raw)) === activeReason)
    .map((row) => ({ row, comment: commentOf(row) }))
    .slice(0, 80);
  return (
    <Panel title={title}>
      <div className="stage-filter loss-filter">
        {lossGroups.map((item) => (
          <button key={item.key} className={lossGroup === item.key ? "active" : ""} onClick={() => {
            setLossGroup(item.key);
            setSelectedReason("");
          }}>
            {item.label}
            <span>{item.value}</span>
          </button>
        ))}
      </div>
      <div className="loss-table">
        {visibleLossData.slice(0, 12).map((item) => (
          <button className={activeReason === item.name ? "active" : ""} key={item.name} onClick={() => setSelectedReason(item.name)}>
            <span>{item.name}</span>
            <b>{item.value}</b>
            <i style={{ width: `${total ? (item.value / total) * 100 : 0}%` }} />
            <em>{total ? `${Math.round((item.value / total) * 1000) / 10}%` : "0%"}</em>
          </button>
        ))}
      </div>
      {activeReason && (
        <div className="loss-comments">
          <h3>{activeReason}: комментарии менеджеров</h3>
          <div className="table-wrap compact-table">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Название</th>
                  <th>Стадия</th>
                  <th>Комментарий</th>
                </tr>
              </thead>
              <tbody>
                {comments.map(({ row, comment }) => (
                  <tr key={`${row.type}-${row.id}`} onClick={() => onSelect(row)}>
                    <td>
                      <BitrixLink type={row.type} id={row.id} />
                    </td>
                    <td>{titleOf(row.type, row.raw)}</td>
                    <td>{stageOf(row.type, row.raw)}</td>
                    <td>{comment || "Комментарий не заполнен"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
}

function bitrixUrl(type: EntityType, id: string) {
  const entityPath = type === "lead" ? "lead" : "deal";
  return `https://b2b-bitrix.outsourcing-kadrov.ru/crm/${entityPath}/details/${id}/`;
}

function BitrixLink({ type, id }: { type: EntityType; id: string }) {
  return (
    <a className="bitrix-link" href={bitrixUrl(type, id)} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
      {id}
    </a>
  );
}

function commentOf(entity: StoredEntity) {
  const priority = [
    "Комментарий",
    "Дополнительно о стадии",
    "Дополнительно об источнике",
    "Описание события",
    "Компания: Комментарий",
    "Контакт: Комментарий",
  ];
  for (const field of priority) {
    const value = entity.raw[field]?.trim();
    if (value) return value;
  }
  const commentField = Object.keys(entity.raw).find((field) => {
    const lower = field.toLowerCase();
    return lower.includes("коммент") || lower.includes("comment");
  });
  return commentField ? entity.raw[commentField] || "" : "";
}

function isLiveDeal(deal: StoredEntity) {
  return ["qualification", "work", "proposal", "agreement"].includes(classifyRecord("deal", deal.raw).key);
}

function liveStageOrder(deal: StoredEntity) {
  const order = { agreement: 1, proposal: 2, work: 3, qualification: 4 } as Record<string, number>;
  return order[classifyRecord("deal", deal.raw).key] || 99;
}

function lastContactDate(entity: StoredEntity) {
  const fields = ["Дата последней коммуникации", "Последняя активность", "Дата изменения стадии", FIELD.changedAt, FIELD.createdAt];
  for (const field of fields) {
    const parsed = parseBitrixDate(value(entity.raw, field));
    if (parsed) return parsed;
  }
  return null;
}

function lastContactText(entity: StoredEntity) {
  const date = lastContactDate(entity);
  return date ? date.toLocaleDateString("ru-RU") : "Нет даты";
}

function daysWithoutContact(entity: StoredEntity) {
  const date = lastContactDate(entity);
  if (!date) return Number.POSITIVE_INFINITY;
  const ms = Date.now() - date.getTime();
  return Math.max(0, Math.floor(ms / 86_400_000));
}

function stageChangedDate(entity: StoredEntity) {
  return parseBitrixDate(value(entity.raw, "Дата изменения стадии")) || lastContactDate(entity);
}

function daysOnStage(entity: StoredEntity) {
  const date = stageChangedDate(entity);
  if (!date) return Number.POSITIVE_INFINITY;
  const ms = Date.now() - date.getTime();
  return Math.max(0, Math.floor(ms / 86_400_000));
}

function stuckLimit(entity: StoredEntity) {
  const key = classifyRecord("deal", entity.raw).key;
  if (key === "qualification") return 7;
  if (key === "work") return 10;
  if (key === "proposal") return 7;
  if (key === "agreement") return 10;
  return 14;
}

function lossGroupFor(type: EntityType, stage: string) {
  const text = stage.toLowerCase().replace(/ё/g, "е");
  if (type === "lead") {
    if (text.includes("некачественный") || text.includes("нецелевой")) return "notTarget";
    if (text.includes("недозвон") || text.includes("закрывают")) return "client";
    return "other";
  }
  if (text.includes("существующий клиент") || text.includes("мониторинг") || text.includes("нужен подбор") || text.includes("не предоставляем") || text.includes("субподряд")) return "notTarget";
  if (text.includes("маленький объем") || text.includes("ставка клиента") || text.includes("не прошли сб") || text.includes("клиент не прошел нашу сб")) return "our";
  if (text.includes("нет потребности") || text.includes("не вышел") || text.includes("не отвечает") || text.includes("выбрали конкурента") || text.includes("будут реализовать сами") || text.includes("конкурент")) return "client";
  return "other";
}

function EntityTable({ rows, onSelect }: { rows: StoredEntity[]; onSelect: (entity: StoredEntity) => void }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Название</th>
            <th>Стадия</th>
            <th>Менеджер</th>
            <th>Источник</th>
            <th>Сумма</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const summary = entitySummary(row);
            return (
              <tr key={`${row.type}-${row.id}`} onClick={() => onSelect(row)}>
                <td><BitrixLink type={row.type} id={row.id} /></td>
                <td>{summary.title}</td>
                <td>{summary.stage}</td>
                <td>{summary.responsible}</td>
                <td>{summary.source}</td>
                <td>{formatMoney(summary.amount)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PivotTable({ rows, type, groupBy, mapping }: { rows: StoredEntity[]; type: EntityType; groupBy: Breakdown; mapping: StageMapping[] }) {
  const stages = funnelDistribution(rows, type).map((item) => item.label);
  const groups = pivotRows(rows, type, groupBy, stages);
  return (
    <div className="table-wrap pivot-wrap">
      <table>
        <thead>
          <tr>
            <th>{BREAKDOWN_LABEL[groupBy]}</th>
            <th>Всего</th>
            {stages.map((stage) => (
              <th key={stage}>{stage}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr key={group.name}>
              <td><b>{group.name}</b></td>
              <td>{group.total}</td>
              {stages.map((stage) => (
                <td key={stage}>{group.stages[stage] || ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LinkedTable({
  deals,
  linkMap,
  leads,
  onSelect,
}: {
  deals: StoredEntity[];
  linkMap: Map<string, EntityLink>;
  leads: StoredEntity[];
  onSelect: (entity: StoredEntity) => void;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Сделка</th>
            <th>Стадия</th>
            <th>Связь</th>
            <th>Лид</th>
            <th>Поля</th>
          </tr>
        </thead>
        <tbody>
          {deals.slice(0, 150).map((deal) => {
            const link = linkMap.get(deal.id);
            const lead = leads.find((item) => item.id === link?.leadId);
            return (
              <tr key={deal.id} onClick={() => onSelect(deal)}>
                <td><BitrixLink type="deal" id={deal.id} /> · {titleOf("deal", deal.raw)}</td>
                <td>{stageOf("deal", deal.raw)}</td>
                <td><Link2 size={14} /> {link?.confidence || "none"}</td>
                <td>
                  {lead ? (
                    <>
                      <BitrixLink type="lead" id={lead.id} /> · {titleOf("lead", lead.raw)}
                    </>
                  ) : (
                    link?.candidateLeadIds.join(", ")
                  )}
                </td>
                <td>{link?.matchedFields.join(", ")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function EntityDrawer({
  entity,
  links,
  leads,
  deals,
  onClose,
}: {
  entity: StoredEntity;
  links: EntityLink[];
  leads: StoredEntity[];
  deals: StoredEntity[];
  onClose: () => void;
}) {
  const summary = entitySummary(entity);
  const relatedLinks = entity.type === "deal" ? links.filter((link) => link.dealId === entity.id) : links.filter((link) => link.leadId === entity.id);
  const relatedEntities =
    entity.type === "deal"
      ? relatedLinks.flatMap((link) => leads.filter((lead) => lead.id === link.leadId || link.candidateLeadIds.includes(lead.id)))
      : relatedLinks.flatMap((link) => deals.filter((deal) => deal.id === link.dealId));
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <button className="close" onClick={onClose}>Закрыть</button>
        <h2>{summary.title}</h2>
        <div className="drawer-meta">
          <span>{entity.type === "lead" ? "Лид" : "Сделка"} #<BitrixLink type={entity.type} id={entity.id} /></span>
          <span>{summary.stage}</span>
          <span>{summary.responsible}</span>
        </div>
        <h3>Связанные записи</h3>
        {relatedEntities.length ? (
          relatedEntities.map((item) => (
            <div className="related" key={`${item.type}-${item.id}`}>
              <b>{item.type === "lead" ? "Лид" : "Сделка"} #<BitrixLink type={item.type} id={item.id} /></b>
              <span>{titleOf(item.type, item.raw)}</span>
            </div>
          ))
        ) : (
          <p className="muted">Связи не найдены.</p>
        )}
        <h3>Поля Битрикса</h3>
        <div className="raw-fields">
          {Object.entries(entity.raw)
            .filter(([, raw]) => raw)
            .map(([field, raw]) => (
              <div key={field}>
                <span>{field}</span>
                <b>{raw}</b>
              </div>
            ))}
        </div>
      </aside>
    </div>
  );
}

function groupKey(row: StoredEntity, breakdown: Breakdown) {
  if (breakdown === "source") return sourceOf(row.raw);
  if (breakdown === "manager") return responsibleOf(row.raw);
  if (breakdown === "project") return row.raw[FIELD.project] || "Без проекта";
  return stageTitle(row.type, row.raw);
}

function breakdownRows(rows: StoredEntity[], type: EntityType, breakdown: Breakdown, mapping: StageMapping[]) {
  if (breakdown === "funnel") return funnelDistribution(rows, type);
  return groupCount(rows, (row) => groupKey(row, breakdown));
}

function pivotRows(rows: StoredEntity[], type: EntityType, breakdown: Breakdown, stages: string[]) {
  const map = new Map<string, { name: string; total: number; stages: Record<string, number> }>();
  rows.forEach((row) => {
    const name = groupKey(row, breakdown);
    const stage = stageTitle(type, row.raw);
    const current = map.get(name) || { name, total: 0, stages: {} };
    current.total += 1;
    current.stages[stage] = (current.stages[stage] || 0) + 1;
    map.set(name, current);
  });
  return [...map.values()]
    .sort((a, b) => b.total - a.total)
    .map((row) => ({
      ...row,
      stages: stages.reduce<Record<string, number>>((acc, stage) => ({ ...acc, [stage]: row.stages[stage] || 0 }), {}),
    }));
}

function formatMoney(value: number) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);
}

export default App;
