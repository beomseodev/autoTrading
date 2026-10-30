const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const {spawn} = require('node:child_process');
const {tmpdir} = require('node:os');
const path = require('node:path');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
fs.mkdirSync('output', {recursive:true});
const server = http.createServer((req, res) => {
  const url = req.url.split('?')[0];
  const file = url === '/' ? 'dashboard.html' : url === '/lab' ? 'index.html' : url.startsWith('/static/') ? url.slice(8) : '';
  if (!/^[\w.-]+$/.test(file) || !fs.existsSync('app/static/' + file)) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync('app/static/' + file));
});
(async () => {
  await new Promise(resolve => server.listen(8777, '127.0.0.1', resolve));
  const profile = fs.mkdtempSync(path.join(tmpdir(), 'loading-check-'));
  const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=9244', '--user-data-dir=' + profile, 'about:blank',
  ], {windowsHide: true, stdio: 'ignore'});
  let socket;
  try {
    let targets;
    for (let i = 0; i < 100; i++) {
      try { targets = await (await fetch('http://127.0.0.1:9244/json/list')).json(); if (targets.some(t => t.type === 'page')) break; } catch {}
      await delay(100);
    }
    assert(targets?.some(t => t.type === 'page'), 'Chrome did not start');
    socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    let sequence = 0;
    const pending = new Map(), errors = [];
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
      const request = pending.get(message.id);
      if (request) { pending.delete(message.id); message.error ? request.reject(message.error) : request.resolve(message.result); }
    };
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence; pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params}));
    });
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails)); return result.result.value;
    };
    const waitFor = async expression => {
      for (let i = 0; i < 100; i++) { if (await evaluate(expression)) return; await delay(50); }
      assert.fail('Timed out: ' + expression);
    };
    await send('Runtime.enable');
    await send('Page.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {source: `
      window.requests = [];
      const nativeSetInterval = window.setInterval;
      window.setInterval = (callback, ms, ...args) => {
        if (ms === 30000) { window.autoRefresh = callback; return 0; }
        return nativeSetInterval(callback, ms, ...args);
      };
      window.fetch = url => new Promise(resolve => requests.push({url, resolve}));
      window.reply = (url, body, ok = true) => {
        const i = requests.findIndex(item => item.url === url);
        if (i < 0) throw new Error('Missing request: ' + url);
        requests.splice(i, 1)[0].resolve({ok, status: ok ? 200 : 500, json: async () => body});
      };
    `});
    await send('Emulation.setDeviceMetricsOverride', {width:1280, height:900, deviceScaleFactor:1, mobile:false});
    await send('Page.navigate', {url:'http://127.0.0.1:8777/'});
    await waitFor('window.requests?.length === 1');
    assert.equal(await evaluate('document.querySelector("#data-loading").hidden'), false);
    await evaluate('reply("/auth/session", {user:null})');
    await waitFor('requests.length === 2');
    await evaluate('autoRefresh(); refreshDashboard(true)');
    assert.equal(await evaluate('requests.length'), 2, 'Initial loading must prevent duplicate refreshes');
    await evaluate('reply("/api/dashboard/markets", {markets:[], macro:[]})');
    assert.equal(await evaluate('document.querySelector("#data-loading").hidden'), false);
    await evaluate('reply("/api/dashboard/portfolio", {holdings:[],complete:true,hasAssets:false,totalValue:0,cashValue:0,baseCurrency:"KRW",fetchedAt:new Date().toISOString(),dayChange:null})');
    await waitFor('document.querySelector("#data-loading").hidden');
    assert.equal(await evaluate('document.querySelector("#refresh-btn").disabled'), false);
    await evaluate('document.querySelector("#refresh-btn").click()');
    assert.deepEqual(await evaluate('requests.map(item => item.url).sort()'), ['/api/dashboard/markets?refresh=true', '/api/dashboard/portfolio?refresh=true'], 'Manual refresh must bypass the cache immediately after initial load');
    assert.match(await evaluate('document.querySelector("#refresh-btn").textContent'), /불러오는 중/);
    fs.writeFileSync('output/loading-desktop.png', Buffer.from((await send('Page.captureScreenshot', {format:'png'})).data, 'base64'));
    await send('Emulation.setDeviceMetricsOverride', {width:390, height:844, deviceScaleFactor:1, mobile:true});
    assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'));
    fs.writeFileSync('output/loading-mobile.png', Buffer.from((await send('Page.captureScreenshot', {format:'png'})).data, 'base64'));
    await send('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    assert.equal(await evaluate('getComputedStyle(document.querySelector("#data-loading"),"::before").animationName'), 'none');
    await evaluate('refreshDashboard(true); autoRefresh()');
    assert.equal(await evaluate('requests.length'), 2, 'In-flight refreshes must not overlap');
    await evaluate('reply("/api/dashboard/portfolio?refresh=true", {detail:"시세 조회 실패"}, false); reply("/api/dashboard/markets?refresh=true", {detail:"시장 조회 실패"}, false)');
    await waitFor('document.querySelector("#data-loading").hidden');
    assert.equal(await evaluate('document.querySelector("#refresh-btn").disabled'), false);
    assert.match(await evaluate('document.querySelector("#app-status").textContent'), /시세 조회 실패/);
    assert.match(await evaluate('document.querySelector("#updated-at").textContent'), /시세 조회 실패/);
    await evaluate('autoRefresh()');
    assert.equal(await evaluate('requests.length'), 0, 'Automatic retry must respect the six-hour interval');
    await evaluate('lastMarketRefresh = lastPortfolioRefresh = Date.now() - 21540000; autoRefresh()');
    assert.equal(await evaluate('requests.length'), 0, 'No automatic refresh before six hours, even after five hours and 59 minutes');
    await evaluate('lastMarketRefresh = lastPortfolioRefresh = Date.now() - 21600001; Object.defineProperty(document,"hidden",{configurable:true,value:true}); autoRefresh()');
    assert.equal(await evaluate('requests.length'), 0, 'Hidden pages must not poll');
    await evaluate('Object.defineProperty(document,"hidden",{configurable:true,value:false}); document.dispatchEvent(new Event("visibilitychange"))');
    await waitFor('requests.some(r => r.url === "/auth/session")');
    await evaluate('reply("/auth/session", {user:null})');
    await waitFor('requests.length === 2');
    const quoteTime = '2026-10-29T20:01:00Z';
    const sample = {holdings:[{ticker:'AAPL', name:'Apple', available:true, quantity:1, averageCost:100, price:348.91, currency:'USD', value:348.91, weightPct:100, profitPct:248.91, asOf:'2026-10-29', fetchedAt:quoteTime}], complete:true, hasAssets:true, totalValue:348.91, cashValue:0, baseCurrency:'USD', fetchedAt:'2026-10-30T01:30:00Z', dayChange:null};
    await evaluate(`reply("/api/dashboard/portfolio", ${JSON.stringify(sample)}); reply("/api/dashboard/markets", {markets:[], macro:[]})`);
    await waitFor('document.querySelector("#data-loading").hidden');
    assert.match(await evaluate('document.querySelector("#holdings-body").textContent'), /2026-10-29/);
    assert.match(await evaluate('document.querySelector("#updated-at").textContent'), /자료 조회/);
    assert.equal(await evaluate('document.querySelector("#updated-at").textContent.includes(dataFetchedAt("2026-10-29T20:01:00Z"))'), true, 'Show quote fetch time rather than response time');
    assert.match(await evaluate('document.querySelector("#valuation-note").textContent'), /6시간마다 자동 갱신/);
    await evaluate('lastMarketRefresh = lastPortfolioRefresh = Date.now() - 21600001; autoRefresh(); autoRefresh()');
    assert.equal(await evaluate('requests.length'), 2, 'Timer refresh must run once after six hours');
    await evaluate(`reply("/api/dashboard/portfolio", ${JSON.stringify(sample)}); reply("/api/dashboard/markets", {markets:[], macro:[]})`);
    await waitFor('document.querySelector("#data-loading").hidden');
    await send('Page.navigate', {url:'http://127.0.0.1:8777/lab'});
    await waitFor('document.querySelectorAll(".position-row").length === 3');
    for (const width of [1280, 390, 320]) {
      await send('Emulation.setDeviceMetricsOverride', {width, height:900, deviceScaleFactor:1, mobile:false});
      assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'), `Lab overflows at ${width}px`);
      fs.writeFileSync(`output/ux-lab-${width}.png`, Buffer.from((await send('Page.captureScreenshot', {format:'png'})).data, 'base64'));
    }
    for (const mode of ['single', 'compare', 'risk']) {
      await evaluate(mode === 'risk' ? 'document.querySelector("#risk-analysis").open = true; void runRiskProfile({preventDefault(){}})' : mode === 'compare' ? 'void runCompareBacktest()' : 'void runBacktest({preventDefault(){}})');
      const button = mode === 'risk' ? '#risk-submit-btn' : '#submit-btn';
      assert.equal(await evaluate(`document.querySelector('${button}').disabled`), true);
      assert.match(await evaluate(`document.querySelector('${button}').textContent`), /분석 중/);
      assert.equal(await evaluate(`getComputedStyle(document.querySelector('${button}'),'::before').content`), '""');
      const url = mode === 'risk' ? '/api/risk-profile' : mode === 'compare' ? '/api/backtests/compare' : '/api/backtests';
      await evaluate(`reply('${url}', {detail:'테스트 조회 실패'}, false)`);
      await waitFor(`!document.querySelector('${button}').disabled`);
      assert.equal(await evaluate(`document.querySelector('${button}').classList.contains('loading-indicator')`), false);
    }
    assert.deepEqual(errors, []);
    console.log('Chrome passed: delayed initial load, concurrent refresh, failure recovery, mobile overflow, reduced motion, all three lab modes.');
  } finally { socket?.close(); chrome.kill(); server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
