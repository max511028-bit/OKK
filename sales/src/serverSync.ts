// Слой синхронизации Dexie ↔ FastAPI + SQLite.
// Сервер — источник правды; Dexie выступает горячим кэшем.
// На старте: pull → bulk-replace всех 7 таблиц.
// На любую запись: schedulePush → debounced PUT каждой таблицы.
import { db } from "./db";
import type {
  ChangeLogEntry,
  EntityLink,
  ImportRun,
  Snapshot,
  StageMapping,
  StoredEntity,
} from "./types";

const API_BASE = "/tasks/api";

function _portalToken(): string {
  // Auth-скрипт (scripts/inject_auth.py) сохраняет токен под ключом 'p_auth'.
  try {
    return sessionStorage.getItem("p_auth") || "";
  } catch {
    return "";
  }
}

async function _apiGet<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path, { cache: "no-store" });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return (await r.json()) as T;
}

async function _apiPut(path: string, body: unknown): Promise<void> {
  const r = await fetch(API_BASE + path, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-Auth-Token": _portalToken(),
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PUT ${path} → ${r.status}`);
}

const COLLECTIONS = [
  "imports",
  "leads",
  "deals",
  "snapshots",
  "changeLog",
  "stageMapping",
  "entityLinks",
] as const;

type CollectionName = (typeof COLLECTIONS)[number];

function tableFor(name: CollectionName) {
  switch (name) {
    case "imports":
      return db.imports;
    case "leads":
      return db.leads;
    case "deals":
      return db.deals;
    case "snapshots":
      return db.snapshots;
    case "changeLog":
      return db.changeLog;
    case "stageMapping":
      return db.stageMapping;
    case "entityLinks":
      return db.entityLinks;
  }
}

let _suppress = false; // не пушим обратно, пока загружаем с сервера

export async function pullFromServer(): Promise<void> {
  _suppress = true;
  try {
    const results = await Promise.all(
      COLLECTIONS.map((n) =>
        _apiGet<unknown[]>(`/sales/${n}`).catch(() => []),
      ),
    );
    await db.transaction(
      "rw",
      [
        db.imports,
        db.leads,
        db.deals,
        db.snapshots,
        db.changeLog,
        db.stageMapping,
        db.entityLinks,
      ],
      async () => {
        for (let i = 0; i < COLLECTIONS.length; i++) {
          const t = tableFor(COLLECTIONS[i]) as any;
          const data = results[i];
          await t.clear();
          if (Array.isArray(data) && data.length > 0) {
            await t.bulkPut(data);
          }
        }
      },
    );
  } finally {
    _suppress = false;
  }
}

let _pushTimer: ReturnType<typeof setTimeout> | null = null;

export function schedulePush(): void {
  if (_suppress) return;
  // Не пытаемся писать на сервер, пока пользователь не залогинился —
  // иначе будем спамить 401 на каждую миграцию Dexie.
  if (!_portalToken()) return;
  if (_pushTimer) clearTimeout(_pushTimer);
  _pushTimer = setTimeout(pushAllToServer, 800);
}

// Когда токен появится (после ввода пароля), хорошо бы запушить накопленные изменения.
// sessionStorage не шлёт storage-событие в той же вкладке, поэтому мягко поллим.
if (typeof window !== "undefined") {
  let _hadToken = !!_portalToken();
  const _tokenPoll = setInterval(() => {
    const now = !!_portalToken();
    if (!_hadToken && now) {
      _hadToken = true;
      clearInterval(_tokenPoll);
      schedulePush();
    }
  }, 700);
  // Через 10 минут перестаём поллить — если пользователь так и не залогинился, выключаем.
  setTimeout(() => clearInterval(_tokenPoll), 10 * 60 * 1000);
}

export async function pushAllToServer(): Promise<void> {
  try {
    const payloads = await Promise.all([
      db.imports.toArray() as Promise<ImportRun[]>,
      db.leads.toArray() as Promise<StoredEntity[]>,
      db.deals.toArray() as Promise<StoredEntity[]>,
      db.snapshots.toArray() as Promise<Snapshot[]>,
      db.changeLog.toArray() as Promise<ChangeLogEntry[]>,
      db.stageMapping.toArray() as Promise<StageMapping[]>,
      db.entityLinks.toArray() as Promise<EntityLink[]>,
    ]);
    await Promise.all(
      COLLECTIONS.map((n, i) =>
        _apiPut(`/sales/${n}`, payloads[i]).catch((e) =>
          console.error(`push ${n} failed:`, e),
        ),
      ),
    );
  } catch (e) {
    console.error("pushAllToServer failed:", e);
  }
}

export function installHooks(): void {
  const tables = [
    db.imports,
    db.leads,
    db.deals,
    db.snapshots,
    db.changeLog,
    db.stageMapping,
    db.entityLinks,
  ];
  tables.forEach((t: any) => {
    t.hook("creating", () => {
      schedulePush();
    });
    t.hook("updating", () => {
      schedulePush();
    });
    t.hook("deleting", () => {
      schedulePush();
    });
  });
}
