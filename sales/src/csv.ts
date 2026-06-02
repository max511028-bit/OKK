import Papa from "papaparse";
import type { ParsedCsv, RawRecord } from "./types";

function dedupeHeaders(rawHeaders: string[]) {
  const counts = new Map<string, number>();
  const warnings: string[] = [];
  const headers = rawHeaders.map((header, index) => {
    const base = (header || "").trim() || `<empty_${index + 1}>`;
    const count = (counts.get(base) || 0) + 1;
    counts.set(base, count);
    if (count === 1) return base;
    const next = `${base}__${count}`;
    warnings.push(`Дублирующаяся колонка "${base}" переименована в "${next}".`);
    return next;
  });
  return { headers, warnings };
}

export function parseCsvFile(file: File): Promise<ParsedCsv> {
  return new Promise((resolve, reject) => {
    Papa.parse<string[]>(file, {
      delimiter: ";",
      skipEmptyLines: "greedy",
      encoding: "utf-8",
      complete: (result) => {
        const rows = result.data.filter((row) => row.some((cell) => String(cell || "").trim()));
        if (!rows.length) {
          resolve({ headers: [], rows: [], warnings: ["Файл пустой."] });
          return;
        }
        const { headers, warnings } = dedupeHeaders(rows[0]);
        const records: RawRecord[] = rows.slice(1).map((row) => {
          const record: RawRecord = {};
          headers.forEach((header, index) => {
            record[header] = String(row[index] ?? "").trim();
          });
          return record;
        });
        const parseWarnings = result.errors.map((error) => `Строка ${error.row}: ${error.message}`);
        resolve({ headers, rows: records, warnings: [...warnings, ...parseWarnings] });
      },
      error: (error) => reject(error),
    });
  });
}
