import type { EntityType, RawRecord } from "./types";

export const FIELD = {
  id: "ID",
  leadStage: "Стадия",
  dealStage: "Стадия сделки",
  leadTitle: "Название лида",
  dealTitle: "Название сделки",
  leadCompany: "Название компании",
  dealCompany: "Компания",
  dealCompanyName: "Компания: Название компании",
  contact: "Контакт",
  createdAt: "Дата создания",
  changedAt: "Дата изменения",
  source: "Источник",
  responsible: "Ответственный",
  project: "Проект",
  amount: "Сумма",
  closed: "Сделка закрыта",
  repeatLead: "Повторный лид",
  repeatDeal: "Повторная сделка",
};

export function value(record: RawRecord, key: string) {
  return (record[key] || "").trim();
}

export function stageOf(type: EntityType, record: RawRecord) {
  return value(record, type === "lead" ? FIELD.leadStage : FIELD.dealStage) || "Без стадии";
}

export function titleOf(type: EntityType, record: RawRecord) {
  return (
    value(record, type === "lead" ? FIELD.leadTitle : FIELD.dealTitle) ||
    value(record, FIELD.leadCompany) ||
    value(record, FIELD.dealCompany) ||
    value(record, FIELD.contact) ||
    "Без названия"
  );
}

export function sourceOf(record: RawRecord) {
  return value(record, FIELD.source) || "Без источника";
}

export function responsibleOf(record: RawRecord) {
  return value(record, FIELD.responsible) || "Без ответственного";
}

export function projectOf(record: RawRecord) {
  return value(record, FIELD.project) || "Без проекта";
}

export function amountOf(record: RawRecord) {
  const raw = value(record, FIELD.amount).replace(/\s/g, "").replace(",", ".");
  const amount = Number(raw);
  return Number.isFinite(amount) ? amount : 0;
}

export function parseBitrixDate(raw: string) {
  const text = raw.trim();
  const match = text.match(/^(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?$/);
  if (!match) return null;
  const [, dd, mm, yyyy, hh = "00", min = "00", ss = "00"] = match;
  const date = new Date(Number(yyyy), Number(mm) - 1, Number(dd), Number(hh), Number(min), Number(ss));
  return Number.isNaN(date.getTime()) ? null : date;
}

export function createdDate(record: RawRecord) {
  return parseBitrixDate(value(record, FIELD.createdAt));
}
