/** Parses a positive integer id from a route/query parameter; null for anything else. */
export function parseId(value: string | null | undefined): number | null {
  if (typeof value !== 'string' || !/^[1-9][0-9]*$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}
