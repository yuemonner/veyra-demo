const test = require('node:test');
const assert = require('node:assert/strict');
const {fetchCaseJSON, readCaseCache, waitForCaseService} = require('../lib/case-data.ts');

test('cache is scoped to its case and rejects corrupt or incompatible views', () => {
  const analysis = {investigation_id:'case-A', cohorts:[], hypotheses:[]};
  const store = {getItem:key => key.endsWith(':case-A') ? JSON.stringify({version:1,saved_at:'2026-10-15',analysis}) : null};
  assert.equal(readCaseCache(store,'case-A').analysis.investigation_id,'case-A');
  assert.equal(readCaseCache(store,'case-B'),null);
  assert.equal(readCaseCache({getItem:()=>JSON.stringify({version:1,saved_at:'now',analysis})},'case-B'),null);
  assert.equal(readCaseCache({getItem:()=>'{bad'},'case-A'),null);
  assert.equal(readCaseCache({getItem:()=>{throw Error('blocked');}},'case-A'),null);
});
test('read timeout aborts the pending fetch instead of spinning indefinitely', async () => {
  const original=global.fetch;
  let aborted=false;
  global.fetch=(_,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>{aborted=true;reject(Error('aborted'));}));
  try { await assert.rejects(fetchCaseJSON('https://example.test','/analysis',undefined,10),/timed out/); assert.equal(aborted,true); } finally {global.fetch=original;}
});
test('uncertain mutation is attempted once, never silently replayed or reported saved', async () => {
  const original=global.fetch; let attempts=0;
  global.fetch=(_,options)=>{attempts++;return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));};
  try { await assert.rejects(fetchCaseJSON('https://example.test','/decision',{owner:'Engineer'},10),/save status is unknown/);assert.equal(attempts,1); } finally {global.fetch=original;}
});
test('404 remains distinguishable for expired session recovery', async () => {
  const original=global.fetch; global.fetch=async()=>({ok:false,status:404,text:async()=>'Case missing'});
  try {await assert.rejects(fetchCaseJSON('','/case'),error=>error.status===404);}finally{global.fetch=original;}
});
test('live response is used and connections are always bounded', async () => {
  const original=global.fetch; global.fetch=async(_,options)=>{assert.equal(options.cache,'no-store');assert.ok(options.signal);return {ok:true,json:async()=>({live:true})};};
  try {assert.deepEqual(await fetchCaseJSON('','/case'),{live:true});}finally{global.fetch=original;}
});
test('proxy HTML is not exposed as an enormous error message', async () => {
  const original=global.fetch;global.fetch=async()=>({ok:false,status:503,text:async()=>'<html>starting service</html>'.repeat(500)});
  try {await assert.rejects(fetchCaseJSON('','/case'),error=>error.message.length<100 && !error.message.includes('<html>'));}finally{global.fetch=original;}
});
test('invalid successful response does not leave an unbounded JSON failure', async () => {
  const original=global.fetch;global.fetch=async()=>({ok:true,json:async()=>{throw SyntaxError('bad HTML');}});
  try {await assert.rejects(fetchCaseJSON('','/case'),/unexpected response/);}finally{global.fetch=original;}
});

test('readiness waits for health without creating or replaying a case', async () => {
  const original=global.fetch; const requests=[];
  global.fetch=async(url,options)=>{requests.push([url,options.method]); return {ok:true,json:async()=>({status:'ok'})};};
  try {await waitForCaseService('https://example.test',30);assert.deepEqual(requests,[['https://example.test/health',undefined]]);}finally{global.fetch=original;}
});
test('readiness rejects an unhealthy service and has a bounded startup budget', async () => {
  const original=global.fetch;
  try {
    global.fetch=async()=>({ok:true,json:async()=>({status:'starting'})});
    await assert.rejects(waitForCaseService('',10),/not ready/);
    global.fetch=(_,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));
    await assert.rejects(waitForCaseService('',10),/timed out/);
  }finally{global.fetch=original;}
});
