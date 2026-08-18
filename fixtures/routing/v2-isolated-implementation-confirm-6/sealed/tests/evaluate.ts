import {pathToFileURL} from 'node:url';
const {scheduleDebounced}=await import(pathToFileURL(process.argv[2]).href);
const calls:string[]=[]; const fn=scheduleDebounced((x:string)=>calls.push(x),5); fn('a'); fn('b'); fn('c'); await new Promise(r=>setTimeout(r,20));
if(calls.join(',')!=='c') throw new Error('latest once');
fn('d'); await new Promise(r=>setTimeout(r,20));
if(calls.join(',')!=='c,d') throw new Error('second burst');
let threw=false; try { scheduleDebounced(()=>{},-1); } catch { threw=true; } if(!threw) throw new Error('negative delay');
