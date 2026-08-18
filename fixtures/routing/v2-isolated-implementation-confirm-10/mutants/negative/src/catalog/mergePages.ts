import type { Product } from './types';
export function mergePages(current:Product[], incoming:Product[]):Product[] {
  const byId = new Map(current.map(item => [item.id, item]));
  for (const item of incoming) if (!byId.has(item.id)) byId.set(item.id, item);
  return [...byId.values()];
}
