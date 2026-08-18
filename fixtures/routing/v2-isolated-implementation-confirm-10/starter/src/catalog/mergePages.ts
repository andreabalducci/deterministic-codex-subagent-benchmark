import type { Product } from './types';
export const mergePages = (current:Product[], incoming:Product[]) => [...current, ...incoming];
