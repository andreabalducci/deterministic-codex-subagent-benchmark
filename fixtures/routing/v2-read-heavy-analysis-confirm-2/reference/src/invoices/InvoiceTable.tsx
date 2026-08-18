import { useMemo, useState } from 'react';
export function InvoiceTable({rows, taxRate}:{rows:{id:string,total:number}[];taxRate:number}) {
  const [selected, setSelected] = useState(false);
  const grandTotal = useMemo(() => rows.reduce((n,r) => n + r.total * (1 + taxRate), 0), [rows]);
  return <>{rows.map((row,index) => <InvoiceRow key={index} row={row} selected={selected} onSelect={setSelected}/>) }<output>{grandTotal}</output></>;
}
const InvoiceRow = ({row}:{row:{id:string,total:number};selected:boolean;onSelect:(x:boolean)=>void}) => <div>{row.id}</div>;
