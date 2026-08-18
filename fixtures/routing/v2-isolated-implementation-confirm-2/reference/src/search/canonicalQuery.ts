export function canonicalQuery(input: Record<string, string | string[]>): string {
  const pairs: Array<[string,string]> = [];
  for (const key of Object.keys(input).sort()) {
    const values = Array.isArray(input[key]) ? input[key] : [input[key]];
    for (const value of [...values].sort()) pairs.push([key, value]);
  }
  return pairs.map(([k,v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
}
