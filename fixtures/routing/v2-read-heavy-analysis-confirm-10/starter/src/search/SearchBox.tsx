import { useTypeahead } from './useTypeahead';
export const SearchBox = ({query}:{query:string}) => <ul>{useTypeahead(query).map(x => <li key={x}>{x}</li>)}</ul>;
