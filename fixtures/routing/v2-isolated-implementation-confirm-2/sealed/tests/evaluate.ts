import {pathToFileURL} from 'node:url';
const {canonicalQuery}=await import(pathToFileURL(process.argv[2]).href);
if(canonicalQuery({b:'2',a:'1'})!=='a=1&b=2') throw new Error('key order');
if(canonicalQuery({tag:['z','a']})!=='tag=a&tag=z') throw new Error('array order');
if(canonicalQuery({q:'a&b'})!=='q=a%26b') throw new Error('encoding');
