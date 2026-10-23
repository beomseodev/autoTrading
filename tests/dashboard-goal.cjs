const assert = require('node:assert/strict');
const {test} = require('node:test');
const {browser, saved} = require('./helpers/dashboard.cjs');

test('current goal position handles zero, partial, achieved and over-target assets', () => {
  for (const initial of [0, 50, 100, 150]) {
    const page = browser(null);
    page.context.markerInitial = initial;
    page.context.markerProjection = {scenarios: [0,1,2].map(i => ({
      label: 'Scenario '+i, annualReturnPct: 5, monthsToGoal: 12, reachedDate: '2027-01-31',
      points: [{date:'2026-01-31',value:initial},{date:'2027-01-31',value:100}],
    }))};
    page.run(`state.goal={currency:'USD',targetAmount:100,monthlyContribution:10,annualReturnPct:5};
      $('#projection-chart').clientWidth=260; $('#projection-chart').clientHeight=120;
      renderProjection(markerProjection,markerInitial)`);
    const svg = page.run('$("#projection-chart").innerHTML');
    assert.match(svg, /class="projection-current"/);
    assert.match(svg, /전망의 시작점/);
    assert.match(svg, new RegExp('현재 '+initial+'%'));
    assert.match(svg, /<line x1="54" y1="16" x2="54"/);
    assert(!svg.includes('NaN'));
    const currentY = Number(svg.match(/<circle cx="54" cy="([^"]+)"/)[1]);
    assert(currentY >= 16 && currentY <= 92);
    assert.equal(page.run('$("#goal-progress").style.values["--current-progress"]'), Math.min(100,initial)+'%');
    assert.match(page.run('$("#goal-current-position").textContent'), new RegExp(initial+'%'));
    page.run('showGoalPending("missing quotes")');
    assert.equal(page.run('$("#goal-progress").style.values["--current-progress"]'), '0%');
    assert.equal(page.run('$("#goal-progress").attributes["aria-valuetext"]'), undefined);
  }
});
