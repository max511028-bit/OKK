export function normalizeText(input: string) {
  return input
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[^0-9a-zа-я]+/g, " ")
    .trim();
}

export function extractPhones(input: string) {
  const phones = new Set<string>();
  const matches = input.match(/\+?\d[\d\s\-()]{6,}\d/g) || [];
  matches.forEach((match) => {
    const digits = match.replace(/\D/g, "");
    if (digits.length >= 10) phones.add(digits.slice(-10));
  });
  return [...phones];
}

export function extractEmails(input: string) {
  const emails = new Set<string>();
  const matches = input.match(/[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}/g) || [];
  matches.forEach((match) => emails.add(match.toLowerCase()));
  return [...emails];
}
