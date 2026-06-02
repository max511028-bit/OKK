import Dexie, { type Table } from "dexie";
import type {
  ChangeLogEntry,
  EntityLink,
  ImportRun,
  Snapshot,
  StageMapping,
  StoredEntity,
} from "./types";

export class CrmDashboardDb extends Dexie {
  imports!: Table<ImportRun, string>;
  leads!: Table<StoredEntity, string>;
  deals!: Table<StoredEntity, string>;
  snapshots!: Table<Snapshot, number>;
  changeLog!: Table<ChangeLogEntry, number>;
  stageMapping!: Table<StageMapping, number>;
  entityLinks!: Table<EntityLink, number>;

  constructor() {
    super("bitrixCrmDashboard");
    this.version(1).stores({
      imports: "id, entityType, createdAt",
      leads: "id, updatedAt, importId",
      deals: "id, updatedAt, importId",
      snapshots: "++id, [entityType+entityId], importId, createdAt",
      changeLog: "++id, [entityType+entityId], importId, field, changedAt",
      stageMapping: "++id, [entityType+stage], entityType, stage, kind",
      entityLinks: "++id, dealId, leadId, confidence",
    });
  }
}

export const db = new CrmDashboardDb();

export async function clearDatabase() {
  await db.transaction(
    "rw",
    [db.imports, db.leads, db.deals, db.snapshots, db.changeLog, db.stageMapping, db.entityLinks],
    async () => {
      await Promise.all([
        db.imports.clear(),
        db.leads.clear(),
        db.deals.clear(),
        db.snapshots.clear(),
        db.changeLog.clear(),
        db.stageMapping.clear(),
        db.entityLinks.clear(),
      ]);
    },
  );
}

export async function exportState() {
  return {
    exportedAt: new Date().toISOString(),
    imports: await db.imports.toArray(),
    leads: await db.leads.toArray(),
    deals: await db.deals.toArray(),
    snapshots: await db.snapshots.toArray(),
    changeLog: await db.changeLog.toArray(),
    stageMapping: await db.stageMapping.toArray(),
    entityLinks: await db.entityLinks.toArray(),
  };
}
