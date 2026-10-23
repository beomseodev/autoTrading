const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {tmpdir} = require('node:os');
const {spawn} = require('node:child_process');
const delay = ms => new Promise(r=>setTimeout(r,ms));
const server = http.createServer((req,res)=>{
  const file=req.url==='/alerts'?'alerts.html':req.url.startsWith('/static/')?req.url.slice(8):'';
  if(!/^[\w.-]+$/.test(file)||!fs.existsSync('app/static/'+file)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');
  res.end(fs.readFileSync('app/static/'+file));
});
(async()=>{
  await new Promise(r=>server.listen(8779,'127.0.0.1',r));
  const profile=fs.mkdtempSync(path.join(tmpdir(),'alert-browser-'));
  const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-first-run','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
  let chromeLog='';chrome.stderr.on('data',data=>chromeLog+=data);
  let socket;
  try{
    let target;
    for(let i=0;i<100;i++){try{const port=fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0];target=(await(await fetch('http://127.0.0.1:'+port+'/json/list')).json()).find(t=>t.type==='page');if(target)break;}catch{}await delay(100);}
    assert(target,'Chrome did not start: '+chromeLog);socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j;});
    let seq=0;const pending=new Map(),errors=[];
    socket.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}};
    const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
    const run=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;};
    const until=async expression=>{for(let i=0;i<150;i++){if(await run(expression))return;await delay(50);}assert.fail('Timed out: '+expression);};
    await send('Runtime.enable');await send('Page.enable');
    const fixture=JSON.parse(fs.readFileSync('tests/fixtures/stock-alert-api.json','utf8'));
    await send('Page.addScriptToEvaluateOnNewDocument',{source:`
      window.fixture=${JSON.stringify(fixture)};window.calls=[];window.watch=[];window.rules=[];window.rejectSave=false;
      window.fetch=async(url,opts={})=>{calls.push({url,...opts});await new Promise(r=>setTimeout(r,150));let data={items:[],nextCursor:null};const method=opts.method||'GET';const body=opts.body?JSON.parse(opts.body):{};
      if(url==='/auth/session')data={user:{sub:'test-user',email:'test@example.com'}};
      else if(url==='/api/alerts/settings')data={...fixture,revision:1,email:'test@example.com',consent:false,sendingEnabled:false};
      else if(url==='/api/alerts/watchlist'){if(method==='POST'){if(rejectSave)return {ok:false,json:async()=>({detail:'종목 조회 실패. 입력은 유지됩니다.'})};watch.push({id:'watch-1',instrumentId:'instrument-1',symbol:body.symbol,metadata:{name:'<img src=x onerror=alert(1)>',mic:'ARCX',currency:'USD'}});}data={items:watch,...watch.at(-1)};}
      else if(url==='/api/alerts/rules'){if(method==='POST')rules.push({id:'rule-1',symbol:'SCHD',kind:body.type,params:body.params,name:body.name,revision:1,enabled:true});data={items:rules,ids:['rule-1']};}
      else if(url==='/api/alerts/rules/rule-1'){if(method==='PATCH')Object.assign(rules[0],body);if(method==='DELETE')rules=[];}
      return {ok:true,status:200,json:async()=>data};};`});
    await send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
    await send('Page.navigate',{url:'http://127.0.0.1:8779/alerts'});
    await until("!document.getElementById('loading').hidden");
    assert(await run("document.getElementById('refresh').disabled"));
    await until("!document.getElementById('content').hidden && document.getElementById('loading').hidden");
    assert.equal(await run("document.getElementById('kind').options.length"),10);
    await run("document.getElementById('symbol').value='SCHD'; document.getElementById('watch-form').requestSubmit()");
    await until("document.querySelector('#watchlist input') && document.getElementById('loading').hidden");
    assert.equal(await run("document.querySelectorAll('#watchlist img').length"),0,'Names must render as text');
    await run("document.getElementById('rule-form').requestSubmit()");
    await until("document.querySelectorAll('#rules button').length===3 && document.getElementById('loading').hidden");
    assert.equal(await run("rules[0].params.period"),200);
    assert.equal(await run("calls.find(c=>c.url==='/api/alerts/rules'&&c.method==='POST').headers['X-Portfolio-Account']"),'test-user');
    await run("document.querySelector('#rules button').click()");
    await until("rules[0].enabled===false && document.getElementById('loading').hidden");
    await run("rejectSave=true;document.getElementById('symbol').value='TQQQ';document.getElementById('watch-form').requestSubmit()");
    await until("document.getElementById('status').textContent.includes('종목 조회 실패')");
    assert.equal(await run("document.getElementById('symbol').value"),'TQQQ');
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    assert(await run('document.documentElement.scrollWidth<=window.innerWidth'),'Mobile overflow');
    fs.mkdirSync('output',{recursive:true});const screenshot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync('output/stock-alerts-mobile.png',Buffer.from(screenshot.data,'base64'));
    assert.deepEqual(errors,[]);console.log('Stock alerts Chrome: loading, ten types, add/bulk rule, pause, escaped names, input preservation, mobile passed.');
  }finally{socket?.close();chrome.kill();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
