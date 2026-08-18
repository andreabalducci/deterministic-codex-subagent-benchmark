import { scheduleDebounced } from './debounceCore';
export function useDebouncedCallback<T extends unknown[]>(fn:(...args:T)=>void, delay:number) { return scheduleDebounced(fn, delay); }
