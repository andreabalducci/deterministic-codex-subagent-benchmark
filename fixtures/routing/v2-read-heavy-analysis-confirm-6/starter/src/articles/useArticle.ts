import { useQuery } from '@tanstack/react-query';
export function useArticle(slug:string, locale:string) {
  return useQuery({ queryKey: ['article', slug], queryFn: () => fetch('/api/articles/' + slug + '?locale=' + locale).then(r => r.json()) });
}
