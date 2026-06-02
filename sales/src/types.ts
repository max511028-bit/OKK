export type EntityType = "lead" | "deal";

export type RawRecord = Record<string, string>;

export interface StoredEntity {
  id: string;
  type: EntityType;
  raw: RawRecord;
  updatedAt: string;
  importId: string;
}

export interface ImportRun {
  id: string;
  entityType: EntityType;
  fileName: string;
  createdAt: string;
  rowCount: number;
  inserted: number;
  updated: number;
  unchanged: number;
  errors: string[];
}

export interface Snapshot {
  id?: number;
  entityType: EntityType;
  entityId: string;
  importId: string;
  createdAt: string;
  raw: RawRecord;
}

export interface ChangeLogEntry {
  id?: number;
  entityType: EntityType;
  entityId: string;
  importId: string;
  field: string;
  oldValue: string;
  newValue: string;
  changedAt: string;
}

export type StageKind = "work" | "success" | "loss" | "service";

export interface StageMapping {
  id?: number;
  entityType: EntityType;
  stage: string;
  kind: StageKind;
  lossReason?: string;
}

export type LinkConfidence = "strong" | "ambiguous" | "weak" | "none";

export interface EntityLink {
  id?: number;
  dealId: string;
  leadId?: string;
  candidateLeadIds: string[];
  confidence: LinkConfidence;
  method: string;
  matchedFields: string[];
  updatedAt: string;
}

export interface ParsedCsv {
  headers: string[];
  rows: RawRecord[];
  warnings: string[];
}

export interface ImportResult {
  importRun: ImportRun;
  linksUpdated: number;
}

export interface DashboardFilters {
  periodFrom: string;
  periodTo: string;
  responsible: string;
  source: string;
  project: string;
  stage: string;
  linkState: "all" | "strong" | "ambiguous" | "weak" | "none";
}
