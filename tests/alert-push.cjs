const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
test('service worker displays generic text and only opens the authenticated alerts route',async()=>{
  const handlers={},shown=[],opened=[];let pending,closed=false;
  const self={addEventListener:(kind,fn)=>handlers[kind]=fn,registration:{showNotification:async(...args)=>shown.push(args)},clients:{openWindow:async url=>opened.push(url)}};
  vm.runInNewContext(fs.readFileSync('app/static/alert-sw.js','utf8'),{self});
  handlers.push({data:{json:()=>({title:'secret account',body:'holding $1000',url:'https://evil.test',tag:'stock-alerts-11111111-1111-1111-1111-111111111111'})},waitUntil:p=>pending=p});await pending;
  assert(!JSON.stringify(shown).includes('secret')&&!JSON.stringify(shown).includes('1000'));
  assert.equal(shown[0][1].tag,'stock-alerts-11111111-1111-1111-1111-111111111111');
  handlers.notificationclick({notification:{data:{url:'https://evil.test'},close:()=>closed=true},waitUntil:p=>pending=p});await pending;
  assert(closed);assert.deepEqual(opened,['/alerts']);
});
