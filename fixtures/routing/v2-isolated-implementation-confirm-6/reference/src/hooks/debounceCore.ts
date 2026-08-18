export function scheduleDebounced<T extends unknown[]>(fn:(...args:T)=>void, delay:number) {
  if (delay < 0) throw new RangeError('delay');
  let timer: ReturnType<typeof setTimeout> | undefined;
  return (...args:T) => { if (timer !== undefined) clearTimeout(timer); timer = setTimeout(() => { timer = undefined; fn(...args); }, delay); };
}
