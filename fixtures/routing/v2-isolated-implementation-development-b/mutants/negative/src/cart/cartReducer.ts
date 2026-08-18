import type { CartState } from './types';
export type Ack = { type: 'ack'; sku: string; quantity: number; revision: number };
export function cartReducer(state: CartState, action: Ack): CartState {
  const current = state.lines[action.sku];
  if (current && action.revision <= current.revision) return state;
  return { ...state, lines: { ...state.lines, [action.sku]: { sku: action.sku, quantity: action.quantity, revision: action.revision } } };
}
