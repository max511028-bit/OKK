import { db } from "./db";
import { parseCsvFile } from "./csv";
import { rebuildEntityLinks } from "./matching";
import type { EntityType, ImportResult, RawRecord, StoredEntity } from "./types";

function idOf(row: RawRecord) {
  return (row.ID || "").trim();
}

function comparableValue(value: unknown) {
  return String(value ?? "").trim();
}

function diffFields(previous: RawRecord, next: RawRecord) {
  const fields = new Set([...Object.keys(previous), ...Object.keys(next)]);
  const changes: Array<{ field: string; oldValue: string; newValue: string }> = [];
  fields.forEach((field) => {
    const oldValue = comparableValue(previous[field]);
    const newValue = comparableValue(next[field]);
    if (oldValue !== newValue) changes.push({ field, oldValue, newValue });
  });
  return changes;
}

export async function importCsv(entityType: EntityType, file: File): Promise<ImportResult> {
  const parsed = await parseCsvFile(file);
  const now = new Date().toISOString();
  const importId = `${entityType}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const table = entityType === "lead" ? db.leads : db.deals;
  const errors = [...parsed.warnings];
  let inserted = 0;
  let updated = 0;
  let unchanged = 0;

  const rows = parsed.rows.filter((row, index) => {
    const id = idOf(row);
    if (id) return true;
    errors.push(`Строка ${index + 2}: нет ID, запись пропущена.`);
    return false;
  });

  await db.transaction("rw", table, db.imports, db.snapshots, db.changeLog, async () => {
    for (const row of rows) {
      const id = idOf(row);
      const previous = await table.get(id);
      const next: StoredEntity = { id, type: entityType, raw: row, updatedAt: now, importId };
      await db.snapshots.add({ entityType, entityId: id, importId, createdAt: now, raw: row });
      if (!previous) {
        await table.put(next);
        inserted += 1;
        continue;
      }
      const changes = diffFields(previous.raw, row);
      if (!changes.length) {
        unchanged += 1;
        await table.put(next);
        continue;
      }
      await table.put(next);
      updated += 1;
      await db.changeLog.bulkAdd(
        changes.map((change) => ({
          entityType,
          entityId: id,
          importId,
          field: change.field,
          oldValue: change.oldValue,
          newValue: change.newValue,
          changedAt: now,
        })),
      );
    }
    await db.imports.add({
      id: importId,
      entityType,
      fileName: file.name,
      createdAt: now,
      rowCount: rows.length,
      inserted,
      updated,
      unchanged,
      errors,
    });
  });

  const linksUpdated = await rebuildEntityLinks();
  const importRun = (await db.imports.get(importId))!;
  return { importRun, linksUpdated };
}
