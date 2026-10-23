const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');

test('holding and allocation names are escaped and tooltip uses the exact percentage', () => {
  const page = browser(null);
  page.context.namedData = {
    hasAssets:true,complete:true,totalValue:100000,cashValue:40000,baseCurrency:'KRW',dayChange:null,fetchedAt:'2026-10-22T00:00:00Z',
    holdings:[{ticker:'005930.KS',name:'삼성전자 <test>',quantity:1,averageCost:50000,available:true,currency:'KRW',price:60000,value:60000,profitPct:20,asOf:'2026-10-22',weightPct:60}],
    rebalance:{items:[{ticker:'005930.KS',name:'삼성전자 <test>',weightPct:60,targetWeightPct:50,driftPct:10,adjustmentValue:-10000}]},
  };
  page.run('renderPortfolio(namedData)');
  assert.match(page.run('$("#holdings-body").innerHTML'), /삼성전자 &lt;test&gt;/);
  assert.match(page.run('$("#rebalance-body").innerHTML'), /삼성전자 &lt;test&gt;/);
  const donut = page.run('$("#allocation-visual").innerHTML');
  assert.match(donut, /data-allocation-label="삼성전자 &lt;test&gt; · 60%"/);
  assert.match(donut, /stroke-dasharray="60 40"/);
  page.context.hoverEvent = {target:{closest:()=>({dataset:{allocationLabel:'삼성전자 <test> · 60%'}})}};
  page.run('$("#allocation-visual").events.pointerover(hoverEvent)');
  assert.equal(page.run('$("#allocation-tooltip").textContent'), '삼성전자 <test> · 60%');
  assert.equal(page.run('$("#allocation-tooltip").hidden'), false);
  page.run('$("#allocation-visual").events.pointerout()');
  assert.equal(page.run('$("#allocation-tooltip").hidden'), true);
});
