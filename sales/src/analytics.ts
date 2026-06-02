import { amountOf, createdDate, FIELD, projectOf, responsibleOf, sourceOf, stageOf, value } from "./fields";
import { inferStageKind } from "./stageDefaults";
import type { DashboardFilters, EntityLink, EntityType, StageMapping, StoredEntity } from "./types";

export interface AppData {
  leads: StoredEntity[];
  deals: StoredEntity[];
  links: EntityLink[];
  mapping: StageMapping[];
}

export function stageKind(entityType: EntityType, stage: string, mapping: StageMapping[]) {
  return mapping.find((item) => item.entityType === entityType && item.stage === stage)?.kind || inferStageKind(stage, entityType);
}

export function lossReason(entityType: EntityType, stage: string, mapping: StageMapping[]) {
  const mapped = mapping.find((item) => item.entityType === entityType && item.stage === stage);
  return mapped?.lossReason || (stageKind(entityType, stage, mapping) === "loss" ? stage : "");
}

export function inPeriod(record: StoredEntity, filters: DashboardFilters) {
  const date = createdDate(record.raw);
  if (!date) return true;
  if (filters.periodFrom && date < new Date(`${filters.periodFrom}T00:00:00`)) return false;
  if (filters.periodTo && date > new Date(`${filters.periodTo}T23:59:59`)) return false;
  return true;
}

export function filterEntity(entity: StoredEntity, filters: DashboardFilters, links: EntityLink[]) {
  if (!inPeriod(entity, filters)) return false;
  if (filters.responsible && responsibleOf(entity.raw) !== filters.responsible) return false;
  if (filters.source && sourceOf(entity.raw) !== filters.source) return false;
  if (filters.project && projectOf(entity.raw) !== filters.project) return false;
  if (filters.stage && stageOf(entity.type, entity.raw) !== filters.stage) return false;
  if (entity.type === "deal" && filters.linkState !== "all") {
    const link = links.find((item) => item.dealId === entity.id);
    const confidence = link?.confidence || "none";
    if (confidence !== filters.linkState) return false;
  }
  return true;
}

export function groupCount<T>(items: T[], getKey: (item: T) => string) {
  const map = new Map<string, number>();
  items.forEach((item) => {
    const key = getKey(item) || "Без значения";
    map.set(key, (map.get(key) || 0) + 1);
  });
  return [...map.entries()]
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);
}

export function getFiltered(data: AppData, filters: DashboardFilters) {
  const leads = data.leads.filter((lead) => filterEntity(lead, filters, data.links));
  const deals = data.deals.filter((deal) => filterEntity(deal, filters, data.links));
  return { leads, deals };
}

export function kpis(data: AppData, filters: DashboardFilters) {
  const { leads, deals } = getFiltered(data, filters);
  const strongLinks = data.links.filter((link) => link.confidence === "strong" && deals.some((deal) => deal.id === link.dealId));
  const linkedLeadIds = new Set(strongLinks.map((link) => link.leadId).filter(Boolean));
  const amount = deals.reduce((sum, deal) => sum + amountOf(deal.raw), 0);
  const wonDeals = deals.filter((deal) => stageKind("deal", stageOf("deal", deal.raw), data.mapping) === "success");
  const lostDeals = deals.filter((deal) => stageKind("deal", stageOf("deal", deal.raw), data.mapping) === "loss");
  return {
    leads: leads.length,
    deals: deals.length,
    strongLinks: strongLinks.length,
    linkedLeads: linkedLeadIds.size,
    linkRate: deals.length ? Math.round((strongLinks.length / deals.length) * 1000) / 10 : 0,
    amount,
    wonDeals: wonDeals.length,
    lostDeals: lostDeals.length,
    conversion: leads.length ? Math.round((strongLinks.length / leads.length) * 1000) / 10 : 0,
  };
}

export function options(data: AppData) {
  const all = [...data.leads, ...data.deals];
  const unique = (values: string[]) => [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru"));
  return {
    responsible: unique(all.map((item) => responsibleOf(item.raw))),
    source: unique(all.map((item) => sourceOf(item.raw))),
    project: unique(all.map((item) => projectOf(item.raw))),
    stage: unique(all.map((item) => stageOf(item.type, item.raw))),
  };
}

export function entitySummary(entity: StoredEntity) {
  return {
    id: entity.id,
    title: value(entity.raw, entity.type === "lead" ? FIELD.leadTitle : FIELD.dealTitle) || "Без названия",
    stage: stageOf(entity.type, entity.raw),
    source: sourceOf(entity.raw),
    responsible: responsibleOf(entity.raw),
    project: projectOf(entity.raw),
    amount: amountOf(entity.raw),
    createdAt: value(entity.raw, FIELD.createdAt),
  };
}
