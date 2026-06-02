import { db } from "./db";
import { FIELD, value } from "./fields";
import { extractEmails, extractPhones, normalizeText } from "./text";
import type { EntityLink, RawRecord, StoredEntity } from "./types";

type Index = Map<string, Set<string>>;

const TRACKING_FIELDS = [
  "ДКТ: client_id",
  "ДКТ: client_sid",
  "ДКТ: client_cid",
  "ДКТ: ya_client_id",
  "roistat",
  "roistat__2",
];

function add(index: Index, key: string, leadId: string) {
  if (!key) return;
  const current = index.get(key) || new Set<string>();
  current.add(leadId);
  index.set(key, current);
}

function valuesFromIndex(index: Index, keys: string[]) {
  const ids = new Set<string>();
  keys.forEach((key) => {
    index.get(key)?.forEach((id) => ids.add(id));
  });
  return ids;
}

function blob(record: RawRecord) {
  return Object.values(record).filter(Boolean).join(" ");
}

function leadTextKeys(record: RawRecord) {
  return [FIELD.leadTitle, FIELD.leadCompany, "Имя", "Фамилия"]
    .map((field) => normalizeText(value(record, field)))
    .filter((item) => item.length >= 6);
}

function dealTextKeys(record: RawRecord) {
  return [FIELD.dealTitle, FIELD.dealCompany, FIELD.dealCompanyName, FIELD.contact, "Контакт: Имя", "Контакт: Фамилия"]
    .map((field) => normalizeText(value(record, field)))
    .filter((item) => item.length >= 6);
}

function trackingKeys(record: RawRecord) {
  const keys: string[] = [];
  Object.entries(record).forEach(([field, rawValue]) => {
    const cleanField = field.replace(/^Компания: /, "").replace(/^Контакт: /, "");
    const baseField = cleanField.replace(/__\d+$/, "");
    const text = String(rawValue || "").trim();
    if (!text) return;
    if (TRACKING_FIELDS.includes(cleanField) || TRACKING_FIELDS.includes(baseField)) {
      keys.push(`${baseField}:${text}`);
    }
  });
  return keys;
}

function createLeadIndexes(leads: StoredEntity[]) {
  const phone = new Map<string, Set<string>>();
  const email = new Map<string, Set<string>>();
  const tracking = new Map<string, Set<string>>();
  const text = new Map<string, Set<string>>();

  leads.forEach((lead) => {
    const leadBlob = blob(lead.raw);
    extractPhones(leadBlob).forEach((item) => add(phone, item, lead.id));
    extractEmails(leadBlob).forEach((item) => add(email, item, lead.id));
    trackingKeys(lead.raw).forEach((item) => add(tracking, item, lead.id));
    leadTextKeys(lead.raw).forEach((item) => add(text, item, lead.id));
  });

  return { phone, email, tracking, text };
}

function setToArray(set: Set<string>) {
  return [...set].sort((a, b) => Number(a) - Number(b));
}

function matchDealWithIndexes(deal: StoredEntity, indexes: ReturnType<typeof createLeadIndexes>): EntityLink {
  const dealBlob = blob(deal.raw);
  const phoneMatches = valuesFromIndex(indexes.phone, extractPhones(dealBlob));
  const emailMatches = valuesFromIndex(indexes.email, extractEmails(dealBlob));
  const trackingMatches = valuesFromIndex(indexes.tracking, trackingKeys(deal.raw));
  const textMatches = valuesFromIndex(indexes.text, dealTextKeys(deal.raw));

  const strong = new Set([...phoneMatches, ...emailMatches, ...trackingMatches]);
  const all = new Set([...strong, ...textMatches]);
  const matchedFields: string[] = [];
  if (phoneMatches.size) matchedFields.push("phone");
  if (emailMatches.size) matchedFields.push("email");
  if (trackingMatches.size) matchedFields.push("tracking");
  if (textMatches.size) matchedFields.push("text");

  const now = new Date().toISOString();
  if (strong.size === 1) {
    return {
      dealId: deal.id,
      leadId: [...strong][0],
      candidateLeadIds: setToArray(strong),
      confidence: "strong",
      method: "single strong signal",
      matchedFields,
      updatedAt: now,
    };
  }
  if (strong.size > 1) {
    return {
      dealId: deal.id,
      candidateLeadIds: setToArray(strong),
      confidence: "ambiguous",
      method: "multiple strong candidates",
      matchedFields,
      updatedAt: now,
    };
  }
  if (all.size === 1) {
    return {
      dealId: deal.id,
      leadId: [...all][0],
      candidateLeadIds: setToArray(all),
      confidence: "weak",
      method: "single weak text signal",
      matchedFields,
      updatedAt: now,
    };
  }
  return {
    dealId: deal.id,
    candidateLeadIds: setToArray(all),
    confidence: all.size > 1 ? "ambiguous" : "none",
    method: all.size > 1 ? "multiple weak candidates" : "no candidates",
    matchedFields,
    updatedAt: now,
  };
}

export async function rebuildEntityLinks() {
  const [leads, deals] = await Promise.all([db.leads.toArray(), db.deals.toArray()]);
  const indexes = createLeadIndexes(leads);
  const links = deals.map((deal) => matchDealWithIndexes(deal, indexes));
  await db.entityLinks.clear();
  if (links.length) await db.entityLinks.bulkAdd(links);
  return links.length;
}
