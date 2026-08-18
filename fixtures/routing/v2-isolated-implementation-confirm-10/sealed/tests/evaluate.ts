import {pathToFileURL} from 'node:url';
const {mergePages}=await import(pathToFileURL(process.argv[2]).href);
const merged=mergePages([{id:'1',name:'old',version:1},{id:'2',name:'keep',version:3}],[{id:'1',name:'new',version:2},{id:'2',name:'stale',version:2},{id:'3',name:'added',version:1}]);
if(merged.length!==3 || merged.find((x:any)=>x.id==='1').name!=='new' || merged.find((x:any)=>x.id==='2').name!=='keep') throw new Error('identity/version merge');
if(merged.filter((x:any)=>x.id==='3').length!==1 || merged.find((x:any)=>x.id==='3').name!=='added') throw new Error('new identity');
