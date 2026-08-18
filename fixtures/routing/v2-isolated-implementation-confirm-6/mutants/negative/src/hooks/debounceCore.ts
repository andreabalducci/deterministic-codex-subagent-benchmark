export function scheduleDebounced<T extends unknown[]>(fn:(...args:T)=>void, delay:number) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return (...args:T) => { if (timer) clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
}
