export function scheduleDebounced<T extends unknown[]>(fn:(...args:T)=>void, delay:number) {
  let timer: ReturnType<typeof setTimeout>;
  return (...args:T) => { timer = setTimeout(() => fn(...args), delay); };
}
