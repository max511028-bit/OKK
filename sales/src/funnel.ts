import { stageOf } from "./fields";
import type { EntityType, RawRecord, StoredEntity } from "./types";

/**
 * Сделка/лид помечены «закрыто» в Bitrix?
 * Поле `Сделка закрыта` в CSV принимает значения `да`/`нет` (для сделок).
 * У лидов аналогичного поля нет — для них всегда false.
 */
export function isClosed(record: RawRecord) {
  const v = (record["Сделка закрыта"] || "").trim().toLowerCase();
  return v === "да" || v === "y" || v === "yes" || v === "true" || v === "1";
}

export interface FunnelStep {
  key: string;
  label: string;
  order: number;
  kind: "active" | "success" | "loss" | "service";
  sourceStages?: string[];
}

const LEAD_STEPS: FunnelStep[] = [
  { key: "new", label: "Поступило лидов", order: 1, kind: "active" },
  { key: "processing", label: "Взято в обработку", order: 2, kind: "active" },
  { key: "contact", label: "Удалось дозвониться", order: 3, kind: "active" },
  { key: "target", label: "Целевой лид", order: 4, kind: "success" },
  { key: "deferred", label: "Отложено", order: 5, kind: "active" },
  { key: "lost", label: "Потеряно", order: 90, kind: "loss" },
  { key: "service", label: "Технические", order: 99, kind: "service" },
];

const DEAL_STEPS: FunnelStep[] = [
  { key: "new", label: "Создана сделка", order: 1, kind: "active" },
  { key: "qualification", label: "Квалификация", order: 2, kind: "active" },
  { key: "work", label: "Проработка", order: 3, kind: "active" },
  { key: "proposal", label: "КП / предложение", order: 4, kind: "active" },
  { key: "agreement", label: "Согласование", order: 5, kind: "active" },
  { key: "success", label: "Договор / старт", order: 6, kind: "success" },
  { key: "lost", label: "Потеряно", order: 90, kind: "loss" },
  { key: "service", label: "Технические", order: 99, kind: "service" },
];

export function funnelSteps(type: EntityType) {
  return type === "lead" ? LEAD_STEPS : DEAL_STEPS;
}

export function normalizeStage(text: string) {
  return text.toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
}

export function classifyStage(type: EntityType, stage: string, closed = false): FunnelStep {
  const normalized = normalizeStage(stage);
  const steps = funnelSteps(type);
  const byKey = (key: string) => steps.find((step) => step.key === key)!;

  if (!normalized || normalized.includes("дубль") || normalized.includes("тест")) return byKey("service");

  if (type === "lead") {
    if (
      normalized.includes("недозвон") ||
      normalized.includes("нецелевой") ||
      normalized.includes("некачественный") ||
      normalized.includes("закрывают потребность")
    ) {
      return byKey("lost");
    }
    if (normalized.includes("не обработан")) return byKey("new");
    if (normalized.includes("в работе") || normalized.includes("поиск контактов")) return byKey("processing");
    if (normalized.includes("обработан") || normalized.includes("связь установлена") || normalized.includes("существующий клиент")) {
      return byKey("contact");
    }
    if (normalized === "качественный лид") return byKey("target");
    if (normalized.includes("отлож")) return byKey("deferred");
    return byKey("processing");
  }

  if (normalized.includes("новая сделка")) return byKey("new");
  if (normalized.includes("существующий клиент") || normalized.includes("нужен подбор") || normalized.includes("мониторинг")) {
    return byKey("lost");
  }
  if (normalized.includes("квалификация") || normalized.includes("поиск контактов")) return byKey("qualification");
  if (normalized.includes("в работе") || normalized.includes("проработка")) {
    return byKey("work");
  }
  if (normalized.includes("кп")) return byKey("proposal");
  if (normalized.includes("согласование")) return byKey("agreement");
  if (
    normalized.includes("вышел первый") ||
    normalized.includes("передан акаунт") ||
    normalized.includes("передан аккаунт") ||
    normalized.includes("проект закончен")
  ) {
    return byKey("success");
  }
  if (normalized.includes("существующий клиент")) return byKey("lost");
  return byKey("lost");
}

/**
 * Классификатор с учётом флага «закрыто» из CRM.
 * Если сделка закрыта (Сделка закрыта = да), но стадия не означает успех / технический — это потеря,
 * даже если по тексту стадии она выглядит «активной» (типа «Поиск контактов»).
 */
export function classifyRecord(type: EntityType, record: RawRecord): FunnelStep {
  const stage = stageOf(type, record);
  const closed = isClosed(record);
  const base = classifyStage(type, stage, closed);
  if (closed && base.kind === "active") {
    const steps = funnelSteps(type);
    return steps.find((step) => step.key === "lost")!;
  }
  return base;
}

export function lossReasonFor(type: EntityType, stage: string) {
  const normalized = normalizeStage(stage);
  if (!normalized) return "Без стадии";
  if (normalized.includes("дубль")) return "Дубль";
  if (normalized.includes("тест")) return "Тест";

  if (type === "lead") {
    if (normalized.includes("недозвон")) return "Не дозвонились";
    if (normalized.includes("нецелевой") || normalized.includes("некачественный")) return "Нецелевой лид";
    if (normalized.includes("закрывают потребность")) return "Закрывают сами";
    if (normalized.includes("отлож")) return "Отложено";
    return "Другая причина";
  }

  if (normalized.includes("нет потребности")) return "Нет потребности";
  if (normalized.includes("не вышел") || normalized.includes("не отвечает")) return "Не выходит на связь";
  if (normalized.includes("маленький объем")) return "Маленький объем";
  if (
    normalized.includes("не предоставляем") ||
    normalized.includes("субподряд") ||
    normalized.includes("существующий клиент") ||
    normalized.includes("мониторинг") ||
    normalized.includes("нужен подбор")
  ) return "Нецелевой для массового персонала";
  if (normalized.includes("дорого") || normalized.includes("ставка")) return "Цена / экономика";
  if (normalized.includes("конкурент")) return "Конкурент";
  if (normalized.includes("сб")) return "Безопасность / отказ";
  if (normalized.includes("тендер")) return "Тендер не состоялся";
  if (normalized.includes("ликвидирована")) return "Компания ликвидирована";
  return "Другая причина";
}

export function funnelDistribution(rows: StoredEntity[], type: EntityType) {
  const counts = new Map(funnelSteps(type).map((step) => [step.key, { ...step, value: 0, sourceStages: [] as string[] }]));
  rows.forEach((row) => {
    const rawStage = stageOf(type, row.raw);
    const step = classifyRecord(type, row.raw);
    const current = counts.get(step.key);
    if (current) {
      current.value += 1;
      if (!current.sourceStages.includes(rawStage)) current.sourceStages.push(rawStage);
    }
  });
  return [...counts.values()].filter((step) => step.value > 0).sort((a, b) => a.order - b.order);
}

export function cumulativeLeadFunnel(leads: StoredEntity[], dealsCreated: number, contracts: number) {
  const nonService = leads.filter((lead) => classifyRecord("lead", lead.raw).kind !== "service");
  const contacted = nonService.filter((lead) => {
    const step = classifyRecord("lead", lead.raw);
    return ["contact", "target", "deferred"].includes(step.key);
  });
  const target = nonService.filter((lead) => classifyRecord("lead", lead.raw).key === "target");
  return [
    { name: "Поступило лидов", value: nonService.length, sourceStages: uniqueStages(nonService, "lead") },
    { name: "Удалось дозвониться", value: contacted.length, sourceStages: uniqueStages(contacted, "lead") },
    { name: "Целевые лиды", value: target.length, sourceStages: uniqueStages(target, "lead") },
    { name: "Создано сделок", value: dealsCreated, sourceStages: ["Все сделки, кроме технических"] },
    { name: "Заключено договоров", value: contracts, sourceStages: ["Вышел первый сотрудник на смену", "Передан аккаунт-отделу", "Проект закончен"] },
  ];
}

export function leadOnlyFunnel(leads: StoredEntity[]) {
  const nonService = leads.filter((lead) => classifyRecord("lead", lead.raw).kind !== "service");
  const contacted = nonService.filter((lead) => {
    const step = classifyRecord("lead", lead.raw);
    return ["contact", "target", "deferred"].includes(step.key);
  });
  const target = nonService.filter((lead) => classifyRecord("lead", lead.raw).key === "target");
  return [
    { key: "new", label: "Поступило лидов", order: 1, kind: "active" as const, value: nonService.length, sourceStages: uniqueStages(nonService, "lead") },
    { key: "contact", label: "Удалось дозвониться", order: 2, kind: "active" as const, value: contacted.length, sourceStages: uniqueStages(contacted, "lead") },
    { key: "target", label: "Целевые лиды", order: 3, kind: "success" as const, value: target.length, sourceStages: uniqueStages(target, "lead") },
  ];
}

export function dealOnlyFunnel(deals: StoredEntity[]) {
  const nonService = deals.filter((deal) => classifyRecord("deal", deal.raw).kind !== "service");
  const rowsFor = (key: string) => nonService.filter((deal) => classifyRecord("deal", deal.raw).key === key);
  const qualification = rowsFor("qualification");
  const work = rowsFor("work");
  const proposal = rowsFor("proposal");
  const agreement = rowsFor("agreement");
  const success = rowsFor("success");
  return [
    { key: "created", label: "Создано сделок", order: 1, kind: "active" as const, value: nonService.length, sourceStages: uniqueStages(nonService, "deal") },
    { key: "qualification", label: "Квалификация", order: 2, kind: "active" as const, value: qualification.length, sourceStages: uniqueStages(qualification, "deal") },
    { key: "work", label: "Проработка", order: 3, kind: "active" as const, value: work.length, sourceStages: uniqueStages(work, "deal") },
    { key: "proposal", label: "КП / предложение", order: 4, kind: "active" as const, value: proposal.length, sourceStages: uniqueStages(proposal, "deal") },
    { key: "agreement", label: "Согласование", order: 5, kind: "active" as const, value: agreement.length, sourceStages: uniqueStages(agreement, "deal") },
    { key: "success", label: "Договор / старт", order: 6, kind: "success" as const, value: success.length, sourceStages: uniqueStages(success, "deal") },
  ];
}

export function stageTitle(type: EntityType, record: RawRecord) {
  return classifyRecord(type, record).label;
}

function uniqueStages(rows: StoredEntity[], type: EntityType) {
  return [...new Set(rows.map((row) => stageOf(type, row.raw)))].filter(Boolean).sort((a, b) => a.localeCompare(b, "ru"));
}
