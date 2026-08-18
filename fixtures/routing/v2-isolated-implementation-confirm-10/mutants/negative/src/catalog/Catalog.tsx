import type { Product } from './types';
export const Catalog = ({items}:{items:Product[]}) => <ul>{items.map(p => <li key={p.id}>{p.name}</li>)}</ul>;
