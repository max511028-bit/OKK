import { db } from "./db";
import type { EntityType, StageKind, StageMapping } from "./types";

const DEFAULTS: Array<Omit<StageMapping, "id">> = [
  { entityType: "lead", stage: "Качественный лид", kind: "success" },
  { entityType: "lead", stage: "Нецелевой клиент", kind: "loss", lossReason: "Нецелевой клиент" },
  { entityType: "lead", stage: "Некачественный лид", kind: "loss", lossReason: "Некачественный лид" },
  { entityType: "lead", stage: "Дубль", kind: "service" },
  { entityType: "lead", stage: "Тест", kind: "service" },
  { entityType: "deal", stage: "КП подготовка и отправка", kind: "work" },
  { entityType: "deal", stage: "Квалификация", kind: "work" },
  { entityType: "deal", stage: "В работе/Проработка", kind: "work" },
  { entityType: "deal", stage: "Согласование договора", kind: "work" },
  { entityType: "deal", stage: "Проект закончен", kind: "success" },
  { entityType: "deal", stage: "Существующий клиент", kind: "success" },
  { entityType: "deal", stage: "Нет потребности в персонале", kind: "loss", lossReason: "Нет потребности" },
  {
    entityType: "deal",
    stage: "Клиент не вышел на встречу/ не отвечает на звонки",
    kind: "loss",
    lossReason: "Не отвечает",
  },
  { entityType: "deal", stage: "Маленький объем", kind: "loss", lossReason: "Маленький объем" },
  {
    entityType: "deal",
    stage: "Не предоставляем такой персонал",
    kind: "loss",
    lossReason: "Не предоставляем персонал",
  },
  { entityType: "deal", stage: "Выбрали конкурента", kind: "loss", lossReason: "Конкурент" },
  { entityType: "deal", stage: "Дубль", kind: "service" },
];

export async function ensureDefaultStageMapping() {
  const count = await db.stageMapping.count();
  if (count === 0) await db.stageMapping.bulkAdd(DEFAULTS);
}

export function inferStageKind(stage: string, entityType: EntityType): StageKind {
  const text = stage.toLowerCase();
  if (text.includes("дубль") || text.includes("тест")) return "service";
  if (text.includes("конкурент") || text.includes("нет потреб") || text.includes("не отвечает")) return "loss";
  if (text.includes("качественный") && entityType === "lead") return "success";
  if (text.includes("проект закончен") || text.includes("существующий клиент")) return "success";
  return "work";
}
