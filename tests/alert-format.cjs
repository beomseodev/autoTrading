const {test} = require('node:test');
const assert = require('node:assert/strict');
const {describeRule, describeValues} = require('../app/static/alert-format.js');
test('conditions explain selected parameters and events without exposing JSON', () => {
  assert.match(describeRule('price_ma_cross', {period:200, average:'sma', direction:'up'}), /200거래일.*상향/);
  assert.match(describeRule('daily_change', {thresholdPct:5, direction:'both'}), /5%/);
  assert.match(describeValues({previousValue:99,value:101,threshold:100}), /전일 값: 99.*당일 값: 101.*기준: 100/);
  assert.equal(describeValues({}), '비교 수치 없음');
});
test('daily change and ranges never display an internal zero as their actual threshold',()=>{
  const text=describeValues({value:6,threshold:0,close:106},'daily_change',{thresholdPct:5,direction:'up'},'USD');
  assert.match(text,/6%/);assert.match(text,/5%/);assert.doesNotMatch(text,/기준: 0/);
  assert.doesNotMatch(describeValues({value:106,threshold:0,previousHigh:100,previousLow:90},'range_breakout',{},'USD'),/기준: 0/);
});
