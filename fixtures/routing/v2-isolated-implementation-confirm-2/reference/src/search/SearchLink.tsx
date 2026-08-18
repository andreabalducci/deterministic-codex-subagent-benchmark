import { canonicalQuery } from './canonicalQuery';
export const SearchLink = ({q}:{q:Record<string,string|string[]>}) => <a href={'/search?' + canonicalQuery(q)}>Search</a>;
