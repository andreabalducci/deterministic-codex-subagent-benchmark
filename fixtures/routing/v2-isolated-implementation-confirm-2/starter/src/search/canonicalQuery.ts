export function canonicalQuery(input: Record<string, string | string[]>): string {
  return Object.entries(input).map(([k,v]) => encodeURIComponent(k) + '=' + encodeURIComponent(String(v))).join('&');
}
