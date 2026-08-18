export function ArticleBody({html}:{html:string}) {
  return <article dangerouslySetInnerHTML={{__html: html}} />;
}
