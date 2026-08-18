import {pathToFileURL} from 'node:url';
const {cartReducer}=await import(pathToFileURL(process.argv[2]).href);
const current={lines:{sku:{sku:'sku',quantity:2,revision:4}}};
if(cartReducer(current,{type:'ack',sku:'sku',quantity:1,revision:3})!==current) throw new Error('stale ack');
if(cartReducer(current,{type:'ack',sku:'sku',quantity:5,revision:4}).lines.sku.quantity!==5) throw new Error('equal correction');
const inserted=cartReducer(current,{type:'ack',sku:'new',quantity:2,revision:1});
if(inserted.lines.new?.quantity!==2 || inserted.lines.sku.quantity!==2) throw new Error('new sku');
