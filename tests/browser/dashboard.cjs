const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const {spawn} = require('node:child_process');

fs.mkdirSync('output', {recursive:true});
const files = {
  '/': ['text/html', fs.readFileSync('app/static/dashboard.html')],
  '/static/dashboard.js': ['text/javascript', fs.readFileSync('app/static/dashboard.js')],
  '/static/loading.css': ['text/css', fs.readFileSync('app/static/loading.css')],
  '/static/dashboard.css': ['text/css', fs.readFileSync('app/static/dashboard.css')],
};
const server = http.createServer((req, res) => {
  const entry = files[req.url.split('?')[0]];
  res.writeHead(entry ? 200 : 404, {'Content-Type': entry?.[0] || 'text/plain'});
  res.end(entry?.[1] || 'Not found');
});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  await new Promise(resolve => server.listen(8766, '127.0.0.1', resolve));
  const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=9233', '--user-data-dir=C:/dev/autoTrading/output/layout-browser-profile', 'about:blank',
  ], {windowsHide: true, stdio: 'ignore'});
  let socket;
  try {
    let targets;
    for (let attempt = 0; attempt < 100; attempt++) {
      try { targets = await (await fetch('http://127.0.0.1:9233/json/list')).json(); if (targets.some(t => t.type === 'page')) break; } catch {}
      await delay(100);
    }
    assert(targets?.some(t => t.type === 'page'), 'Chrome debugging endpoint did not start');
    socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    let sequence = 0;
    const pending = new Map();
    const errors = [];
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
      if (pending.has(message.id)) {
        const {resolve, reject} = pending.get(message.id);
        pending.delete(message.id);
        message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
      }
    };
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params}));
    });
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    await send('Runtime.enable');
    await send('Page.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {source: `window.fetch = async (url, options) => {
      window.fixtureRequests = (window.fixtureRequests || 0) + 1;
      let result;
      const accountTest = location.hash === '#layout-account-test';
      if (url === '/auth/session') result = {user:accountTest ? {sub:'layout-browser-fixture',email:'layout-test@example.invalid'} : null, isOwner:false};
      else if (url === '/api/dashboard/state' && accountTest) {
        if (sessionStorage.getItem('recovery-offline')) return {ok:false,status:503,json:async()=>({detail:'검증용 연결 장애'})};
        if (options?.method === 'POST') {
          const body = JSON.parse(options.body);
          sessionStorage.setItem('layout-fixture-state',JSON.stringify(body.state));
          result = {revision:'fixture-r1'};
        } else result = {configured:true,emailReady:false,revision:'fixture-r1',state:JSON.parse(sessionStorage.getItem('layout-fixture-state') || '{"version":1,"portfolio":{"positions":[],"cashKRW":0,"cashUSD":0,"baseCurrency":"KRW"},"goal":null,"portfolioSavedAt":null}')};
      }
      else if (url === '/api/dashboard/markets') result = {markets:[1,2,3,4].map(i => ({label:'Market '+i, value:100, changePct:1, asOf:'2026-10-22', available:true, source:'#'})), macro:[]};
      else if (url === '/api/dashboard/portfolio') result = {holdings:[], complete:true, hasAssets:false, totalValue:0, cashValue:0, baseCurrency:'KRW', fetchedAt:new Date().toISOString(), dayChange:null};
      else throw new Error('Unexpected API: '+url);
      return {ok:true,status:200,json:async()=>result};
    };`});
    await send('Emulation.setDeviceMetricsOverride', {width:1280, height:1000, deviceScaleFactor:1, mobile:false});
    await send('Page.navigate', {url:process.argv[2] || 'http://127.0.0.1:8766/'});
    let handles = 0;
    for (let attempt = 0; attempt < 100; attempt++) {
      handles = await evaluate('document.querySelectorAll(".panel-resize").length');
      if (handles === 8) break;
      await delay(50);
    }
    assert.equal(handles, 8);
    await evaluate('document.documentElement.style.scrollBehavior="auto"');
    const before = await evaluate(`(() => {
      const panel = document.querySelector('[data-panel="goal"]');
      panel.querySelector('button.panel-resize').scrollIntoView({block:'center'});
      const rect = panel.getBoundingClientRect();
      const h = panel.querySelector('.panel-resize').getBoundingClientRect();
      return {width:rect.width,height:rect.height,x:h.x+h.width/2,y:h.y+h.height/2};
    })()`);
    await send('Input.dispatchMouseEvent', {type:'mouseMoved', x:before.x, y:before.y});
    await send('Input.dispatchMouseEvent', {type:'mousePressed', x:before.x, y:before.y, button:'left', clickCount:1});
    await send('Input.dispatchMouseEvent', {type:'mouseMoved', x:before.x+120, y:before.y+80, button:'left', buttons:1});
    await send('Input.dispatchMouseEvent', {type:'mouseReleased', x:before.x+120, y:before.y+80, button:'left', clickCount:1});
    const resized = await evaluate(`(() => { const r=document.querySelector('[data-panel="goal"]').getBoundingClientRect();return {width:r.width,height:r.height};})()`);
    assert(resized.width > before.width, 'Pointer drag did not increase width');
    assert(resized.height > before.height, 'Pointer drag did not increase height');
    await send('Input.dispatchKeyEvent', {type:'keyDown',key:'ArrowDown',code:'ArrowDown',windowsVirtualKeyCode:40});
    await send('Input.dispatchKeyEvent', {type:'keyUp',key:'ArrowDown',code:'ArrowDown',windowsVirtualKeyCode:40});
    const keyboardHeight = await evaluate(`document.querySelector('[data-panel="goal"]').getBoundingClientRect().height`);
    assert(keyboardHeight >= resized.height + 19);
    assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Desktop layout overflows horizontally');
    fs.writeFileSync('output/layout-desktop.png', Buffer.from((await send('Page.captureScreenshot', {format:'png',captureBeyondViewport:false})).data,'base64'));
    await evaluate('document.querySelector("#reset-layout").click()');
    assert.equal(await evaluate('Object.keys(panelLayout).length'), 0);
    const requestsBeforeResize = await evaluate('window.fixtureRequests');
    await evaluate(`window.adaptiveSnapshot = {
      hasAssets:true,complete:true,baseCurrency:'KRW',totalValue:100000,cashValue:50000,
      unrealizedProfit:5000,unrealizedProfitPct:11.1,dayChange:0,dayChangePct:0,fetchedAt:'2026-10-22T00:00:00Z',
      holdings:Array.from({length:5},(_,i)=>({ticker:'TEST'+i,name:['삼성전자','애플','마이크로소프트','테스트 종목3','테스트 종목4'][i],available:true,currency:'KRW',price:10000,averageCost:9000,quantity:1,asOf:'2026-10-22',value:10000,profitPct:11.1,weightPct:10})),
      rebalance:{message:'목표 비중 점검 필요',asOf:'2026-10-22',thresholdPct:5,items:Array.from({length:5},(_,i)=>({ticker:'TEST'+i,name:['삼성전자','애플','마이크로소프트','테스트 종목3','테스트 종목4'][i],weightPct:10,targetWeightPct:20,driftPct:-10,adjustmentValue:10000,needsRebalance:true}))}
    }; renderPortfolio(adaptiveSnapshot);
    renderMarket({markets:[1,2,3,4].map(i=>({label:'Market '+i,value:100,changePct:1,asOf:'2026-10-22',available:true,source:'#'})),macro:[1,2,3,4].map(i=>({label:'Macro '+i,value:3.5,change:0.1,asOf:'2026-10-01',available:true,description:'Detailed macro explanation',source:'#'}))});
    state.goal={currency:'KRW',targetAmount:200000,monthlyContribution:10000,annualReturnPct:5};
    window.adaptiveProjection={scenarios:[0,1,2].map((i)=>({label:'Scenario '+i,annualReturnPct:i*3+2,monthsToGoal:12,reachedDate:'2027-10-31',points:[{date:'2026-10-22',value:100000},{date:'2027-04-30',value:140000+i*10000},{date:'2027-10-31',value:200000+i*10000}]}))};
    renderProjection(adaptiveProjection,100000);
    resizePanel('holdings',3,200); resizePanel('macro',3,200); resizePanel('allocation',4,220); resizePanel('goal',4,200);`);
    await delay(150);
    const display = selector => evaluate(`getComputedStyle(document.querySelector(${JSON.stringify(selector)})).display`);
    assert.equal(await display('#holdings .table-scroll'), 'none');
    assert.notEqual(await display('#holdings-compact'), 'none');
    assert.match(await evaluate('document.querySelector("#holdings-compact").textContent'), /2026-10-22/);
    await evaluate('renderPortfolio({...adaptiveSnapshot,complete:false,totalValue:null})');
    assert.match(await evaluate('document.querySelector("#holdings-compact").textContent'), /합계 계산 보류/);
    await evaluate('renderPortfolio(adaptiveSnapshot)');
    assert.equal(await display('#projection-chart'), 'none');
    assert.match(await evaluate('document.querySelector("#goal-current-position").textContent'), /50%/);
    assert.notEqual(await display('#goal-current-position'), 'none');
    assert.equal(await display('.macro-meaning'), 'none');
    assert.equal(await display('#allocation-visual'), 'none');
    assert.equal(await display('.allocation-line:nth-child(4)'), 'none');
    await evaluate('document.querySelector("#holdings").scrollIntoView({block:"center"})');
    fs.writeFileSync('output/layout-adaptive-compact.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'));
    await evaluate(`resizePanel('holdings',4,600);resizePanel('goal',4,600);`);
    await delay(150);
    assert.equal(await display('#holdings thead'), 'none');
    assert.equal(await display('#holdings td:nth-child(2)'), 'none');
    assert.equal(await display('#holdings tr'), 'grid');
    assert.notEqual(await display('#holdings td:nth-child(3)'), 'none');
    assert.equal(await display('.projection-detail'), 'none');
    assert.equal(await display('.scenario-result:not(.base)'), 'none');
    await evaluate(`resizePanel('holdings',12,600);resizePanel('macro',12,500);resizePanel('allocation',12,600);resizePanel('goal',12,700);`);
    await delay(150);
    assert.notEqual(await display('#holdings thead'), 'none');
    assert.notEqual(await display('#holdings td:nth-child(2)'), 'none');
    assert.notEqual(await display('#holdings td:nth-child(6)'), 'none');
    assert.notEqual(await display('.price-cost-detail'), 'none');
    assert.notEqual(await display('.macro-meaning'), 'none');
    assert.notEqual(await display('#allocation-visual'), 'none');
    assert.notEqual(await display('.allocation-line:nth-child(4)'), 'none');
    assert.notEqual(await display('.projection-detail'), 'none');
    assert.notEqual(await display('.scenario-result:not(.base)'), 'none');
    assert.match(await evaluate('document.querySelector("#holdings-body").textContent'), /삼성전자/);
    assert.match(await evaluate('document.querySelector("#rebalance-body").textContent'), /삼성전자/);
    assert.match(await evaluate('document.querySelector(".projection-current").getAttribute("aria-label")'), /50%/);
    const currentLine = await evaluate(`(() => {const line=document.querySelector('.projection-current line');return [line.getAttribute('x1'),line.getAttribute('x2')];})()`);
    assert.equal(currentLine[0],currentLine[1]);
    await evaluate('document.querySelector(".allocation-donut").scrollIntoView({block:"center"})');
    const hover = await evaluate(`(() => {
      const segment=[...document.querySelectorAll('.allocation-segment')].find(item=>item.dataset.allocationLabel.includes('삼성전자'));
      const r=segment.closest('svg').getBoundingClientRect();
      const start=-Number(segment.getAttribute('stroke-dashoffset'));
      const percent=Number(segment.getAttribute('stroke-dasharray').split(' ')[0]);
      const angle=(start+percent/2)/100*2*Math.PI;
      return {x:r.x+r.width/2+Math.sin(angle)*r.width*75/180,y:r.y+r.height/2-Math.cos(angle)*r.height*75/180};
    })()`);
    await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:hover.x,y:hover.y});
    assert.equal(await evaluate('document.querySelector("#allocation-tooltip").hidden'),false);
    assert.equal(await evaluate('document.querySelector("#allocation-tooltip").textContent'),'삼성전자 · 10%');
    fs.writeFileSync('output/allocation-tooltip.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'));
    await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:1,y:1});
    assert.equal(await evaluate('document.querySelector("#allocation-tooltip").hidden'),true);
    await evaluate(`document.querySelectorAll('.allocation-segment')[1].focus()`);
    assert.equal(await evaluate('document.querySelector("#allocation-tooltip").hidden'),false);
    await evaluate(`document.querySelectorAll('.allocation-segment')[1].blur()`);
    const chartHeight = await evaluate('document.querySelector("#projection-chart svg").viewBox.baseVal.height');
    assert(chartHeight > 160, 'Goal graph height did not expand with its section');
    await evaluate(`resizePanel('goal',12,900)`);
    await delay(150);
    assert(await evaluate('document.querySelector("#projection-chart svg").viewBox.baseVal.height') > chartHeight, 'Graph ignores height-only changes');
    assert.equal(await evaluate('window.fixtureRequests'), requestsBeforeResize, 'Resizing triggered unnecessary data requests');
    await evaluate('document.querySelector("#goal").scrollIntoView({block:"center"})');
    fs.writeFileSync('output/layout-adaptive-wide.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'));
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:false});
    await delay(100);
    await evaluate('resizePanel("goal", 3, 300)');
    const mobile = await evaluate(`(() => {
      const grid=document.querySelector('.overview-grid').getBoundingClientRect();
      return [...document.querySelectorAll('.dashboard-panel:not([hidden])')].map(panel => ({width:panel.getBoundingClientRect().width,grid:grid.width}));
    })()`);
    assert(mobile.every(panel => Math.abs(panel.width-panel.grid) < 1), 'Mobile panels do not fill one column');
    assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile page overflows horizontally');
    await evaluate('document.querySelector("#reset-layout").scrollIntoView({block:"start"})');
    fs.writeFileSync('output/layout-mobile.png', Buffer.from((await send('Page.captureScreenshot', {format:'png',captureBeyondViewport:false})).data,'base64'));
    await evaluate(`state = emptyState();
      state.portfolio.positions = ['QQQI','SCHD'].map(ticker=>({ticker,quantity:1,averageCost:0}));
      openPortfolio(); document.querySelector('#add-rebalance-group').click();
      document.querySelector('[name=groupName]').value='배당 ETF';
      document.querySelector('[name=groupTarget]').value='60';
      document.querySelectorAll('[name=groupMember]').forEach(input=>input.click());`);
    assert.equal(await evaluate('document.querySelectorAll("[name=groupMember]:checked").length'),2);
    assert(await evaluate('document.querySelector("#portfolio-form").checkValidity()'));
    await evaluate('document.querySelector(".rebalance-group").scrollIntoView({block:"center"})');
    assert(await evaluate('document.querySelector("#portfolio-dialog").scrollWidth <= document.querySelector("#portfolio-dialog").clientWidth'), 'Group editor overflows on mobile');
    fs.writeFileSync('output/rebalance-group-mobile.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'));
    await evaluate('document.querySelector("#portfolio-form").requestSubmit()');
    await delay(200);
    assert.equal(await evaluate('document.querySelector("#portfolio-dialog").open'),false);
    assert.deepEqual(await evaluate('state.portfolio.rebalanceGroups'),[{name:'배당 ETF',tickers:['QQQI','SCHD'],targetWeight:60}]);
    assert.match(await evaluate('document.querySelector("#rebalance-settings").textContent'),/배당 ETF.*60%.*현금 40%/);
    await evaluate('openPortfolio()');
    assert.equal(await evaluate('document.querySelectorAll("[name=groupMember]:checked").length'),2);
    assert.equal(await evaluate('document.querySelector("[name=groupTarget]").value'),'60');
    await evaluate('document.querySelector(".rebalance-group button").click()');
    assert.equal(await evaluate('document.querySelectorAll(".rebalance-group").length'),0);
    await evaluate('document.querySelector("#portfolio-dialog").close()');
    await evaluate('document.querySelector("#reset-layout").click()');
    const dragPoints = await evaluate(`(() => {
      document.querySelector('[data-panel="market"]').scrollIntoView({block:'start'});
      const handle=document.querySelector('[data-panel="summary"] .panel-move').getBoundingClientRect();
      const target=document.querySelector('[data-panel="market"]').getBoundingClientRect();
      return {x:handle.x+handle.width/2,y:handle.y+handle.height/2,tx:target.x+target.width/2,ty:target.y+5};
    })()`);
    await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:dragPoints.x,y:dragPoints.y});
    await send('Input.dispatchMouseEvent',{type:'mousePressed',x:dragPoints.x,y:dragPoints.y,button:'left',clickCount:1});
    await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:dragPoints.tx,y:dragPoints.ty,button:'left',buttons:1});
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:dragPoints.tx,y:dragPoints.ty,button:'left',clickCount:1});
    assert.equal(await evaluate('document.querySelector(".overview-grid").firstElementChild.dataset.panel'),'summary');
    await evaluate(`document.querySelector('[data-panel="summary"] .panel-move').focus()`);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowDown',code:'ArrowDown',windowsVirtualKeyCode:40});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowDown',code:'ArrowDown',windowsVirtualKeyCode:40});
    assert.equal(await evaluate('document.querySelector(".overview-grid").firstElementChild.dataset.panel'),'market');
    await evaluate(`document.querySelector('[data-panel="goal"] .panel-controls button:last-child').click()`);
    assert.equal(await evaluate('document.querySelector("[data-panel=goal]").hidden'),true);
    await evaluate(`document.querySelector('#widget-list-button').click(); document.querySelector('[data-widget="goal"]').click(); document.querySelector('[data-widget="performance"]').click()`);
    assert.equal(await evaluate('document.querySelector("[data-panel=goal]").hidden'),false);
    assert.equal(await evaluate('document.querySelector("[data-panel=performance]").hidden'),false);
    assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'));
    await evaluate('document.querySelector("#widget-list").scrollIntoView({block:"start"})');
    fs.writeFileSync('output/widget-list-mobile.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'));
    await send('Page.navigate',{url:(process.argv[2] || 'http://127.0.0.1:8766/')+'?layout-test='+Date.now()+'#layout-account-test'});
    for (let attempt=0;attempt<100;attempt++) {
      if (await evaluate('typeof layoutReady!=="undefined" && layoutReady && !cloudLoading && accountSub==="layout-browser-fixture"')) break;
      await delay(50);
    }
    await evaluate(`resizePanel('goal',6,500); layoutChanged(); movePanel('goal','market'); setWidgetVisible('macro',false); setWidgetVisible('performance',true)`);
    await delay(700);
    assert.match(await evaluate('document.querySelector("#layout-status").textContent'),/자동 저장했습니다/);
    await evaluate('window.__reloadMarker=true');
    await send('Page.reload');
    for (let attempt=0;attempt<100;attempt++) {
      if (await evaluate('!window.__reloadMarker && typeof layoutReady!=="undefined" && layoutReady && !cloudLoading')) break;
      await delay(50);
    }
    assert.equal(await evaluate('document.querySelector(".overview-grid").firstElementChild.dataset.panel'),'goal');
    assert.equal(await evaluate('document.querySelector("[data-panel=macro]").hidden'),true);
    assert.equal(await evaluate('document.querySelector("[data-panel=performance]").hidden'),false);
    assert.equal(await evaluate('panelLayout.goal.height'),500);
    await evaluate(`sessionStorage.setItem('recovery-offline','1'); openPortfolio();
      document.querySelector('#portfolio-form [name=cashUSD]').value='200';
      document.querySelector('#portfolio-form').requestSubmit();`);
    await delay(150);
    assert.equal(await evaluate('document.querySelector("#portfolio-dialog").open'),true);
    assert.equal(await evaluate('pendingDraft.state.portfolio.cashUSD'),200);
    await evaluate('document.querySelector("#portfolio-recovery-btn").click()');
    assert.equal(await evaluate('document.querySelector("#portfolio-dialog").open'),false);
    assert.match(await evaluate('document.querySelector("#cloud-status").textContent'),/초안/);
    await evaluate('sessionStorage.removeItem("recovery-offline"); window.__reloadMarker=true');
    await send('Page.reload');
    for (let attempt=0;attempt<100;attempt++) {
      if (await evaluate('!window.__reloadMarker && typeof layoutReady!=="undefined" && layoutReady && !cloudLoading')) break;
      await delay(50);
    }
    assert.equal(await evaluate('state.portfolio.cashUSD'),200);
    assert.match(await evaluate('document.querySelector("#draft-preview").textContent'),/200 USD/);
    assert.match(await evaluate('document.querySelector("#server-preview").textContent'),/0 USD/);
    await evaluate('document.querySelector("#recovery-panel").scrollIntoView({block:"start"})');
    assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Recovery view overflows on mobile');
    fs.writeFileSync('output/recovery-mobile.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'));
    await send('Emulation.setDeviceMetricsOverride', {width:1280,height:1000,deviceScaleFactor:1,mobile:false});
    await delay(100);
    fs.writeFileSync('output/recovery-desktop.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'));
    await evaluate('document.querySelector("#use-server-btn").focus(); document.querySelector("#use-server-btn").click()');
    assert.equal(await evaluate('state.portfolio.cashUSD'),0);
    assert.equal(await evaluate('pendingDraft'),null);
    assert(await evaluate('Object.keys(localStorage).some(k=>k.startsWith(STORAGE_KEY+"-backup:"))'));
    await evaluate('sessionStorage.removeItem("layout-fixture-state"); Object.keys(localStorage).filter(k=>k.startsWith(STORAGE_KEY)).forEach(k=>localStorage.removeItem(k))');
    assert.deepEqual(errors, []);
    console.log('Real Chrome: resizing, compact summaries, partial-data warnings, responsive table columns, scenario detail, chart height, no extra requests and desktop/mobile overflow passed.');
  } finally {
    socket?.close(); chrome.kill(); server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
