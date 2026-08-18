export const sortInvoices = <T extends {id:string}>(rows:T[]) => [...rows].sort((a,b)=>a.id.localeCompare(b.id));
