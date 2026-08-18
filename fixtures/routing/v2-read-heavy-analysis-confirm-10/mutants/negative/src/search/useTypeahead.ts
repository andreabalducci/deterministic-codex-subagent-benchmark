import { useEffect, useRef, useState } from 'react';
export function useTypeahead(query:string) {
  const controller = useRef(new AbortController());
  const [results,setResults] = useState<string[]>([]);
  useEffect(() => {
    controller.current.abort();
    fetch('/api/search?q=' + encodeURIComponent(query), {signal: controller.current.signal}).then(r => r.json()).then(setResults);
  }, [query]);
  return results;
}
