// Verify user-facing network/auth errors without contacting Telegram or a provider.
const fs=require('node:fs');
const assert=require('node:assert/strict');
const source=fs.readFileSync('bot/static/index.html','utf8');
const apiSource=source.slice(source.indexOf('async function api('),source.indexOf('function node('));
const makeApi=new Function('raw','fetch',apiSource+';return api');
async function run(){
 await assert.rejects(makeApi('',()=>{})('/api/summary'),/Telegram/);
 await assert.rejects(makeApi('synthetic-auth',async()=>{throw Error('Failed to fetch')})('/api/summary'),/Нет связи с сервером/);
 await assert.rejects(makeApi('synthetic-auth',async()=>({json:async()=>{throw Error('HTML gateway response')}}))('/api/summary'),/HTTPS-туннель/);
 await assert.rejects(makeApi('synthetic-auth',async()=>({ok:false,json:async()=>({error:'Неверная авторизация'})}))('/api/summary'),/Неверная авторизация/);
 const value=await makeApi('synthetic-auth',async()=>({ok:true,json:async()=>({cash_flow:10000})}))('/api/summary');
 assert.equal(value.cash_flow,10000);
 console.log('Mini App network and authorization error checks: 5 passed');
}
run().catch(e=>{console.error(e);process.exitCode=1});
