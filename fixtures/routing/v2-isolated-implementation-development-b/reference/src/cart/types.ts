export type CartLine = { sku: string; quantity: number; revision: number };
export type CartState = { lines: Record<string, CartLine> };
