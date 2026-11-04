const {test} = require('node:test');
const assert = require('node:assert/strict');
const {dailyReports, prices, forecasts, levelState, chartMarkup} = require('../static/research-dashboard.js');
const make = (date, revision=1, rest={}) => ({symbol:'TEST.US',trade_date:date,revision,...rest});
test('highest daily revision wins, but the explicitly selected revision is preserved',()=>{
  const selected=make('2026-11-03',1);
  const reports=[make('2026-11-02',1),make('2026-11-02',2),make('2026-11-03',2),make('2026-11-04'),{...make('2026-11-01'),symbol:'OTHER.US'}];
  assert.deepEqual(dailyReports(reports,selected).map(r=>[r.trade_date,r.revision]),[['2026-11-02',2],['2026-11-03',1]]);
});
test('historical price series uses selected source and excludes future or invalid prices',()=>{
  const selected=make('2026-11-03',1,{market_snapshot:{close:102},price_history:[{date:'2026-11-02',close:100},{date:'2026-11-03',close:101},{date:'2026-11-04',close:999},{date:'2026-11-01',close:null}]});
  const result=prices(selected,[make('2026-10-30',1,{market_snapshot:{close:500}})]);
  assert.equal(result.fromHistory,true);
  assert.deepEqual(result.points,[{date:'2026-11-02',close:100},{date:'2026-11-03',close:102}]);
});
test('missing continuous prices fall back to sparse report snapshots without inventing dates',()=>{
  const selected=make('2026-11-03',1,{market_snapshot:{close:100}});
  assert.deepEqual(prices(selected,[make('2026-10-30',1,{market_snapshot:{close:90}})]).points,[{date:'2026-10-30',close:90},{date:'2026-11-03',close:100}]);
});
test('scenario range never substitutes for an absent research target',()=>{
  const report=make('2026-11-03',1,{price_levels:{status:'unavailable',target:null},scenarios:[{price:120},{price:90},{price:null}]});
  const [point]=forecasts([],report);
  assert.equal(point.target,null);assert.equal(point.low,90);assert.equal(point.high,120);
});
test('null and unavailable predictions remain gaps in the SVG',()=>{
  const svg=chartMarkup([{date:'2026-11-01',target:10},{date:'2026-11-02',target:null},{date:'2026-11-03',target:12}],[{key:'target',label:'target',color:'#fff'}],'chart','USD');
  const path=svg.match(/<path d="([^"]+)"/)[1];
  assert.equal((path.match(/M/g)||[]).length,2);assert.ok(!path.includes('L'));assert.ok(!svg.includes('NaN'));
});
test('expired entry plans cannot appear as currently active',()=>{
  assert.match(levelState(make('2026-11-02',1,{price_levels:{status:'conditional',entry_valid_until:'2026-11-03'}}),'2026-11-04'),/有效期已过/);
  assert.match(levelState(make('2026-11-03',1,{price_levels:{status:'conditional',entry_valid_until:'2026-11-04'}}),'2026-11-04'),/等待触发/);
  assert.match(levelState(make('2026-11-03',1,{price_levels:{status:'unavailable'}}),'2026-11-04'),/未提供/);
});
test('single point charts are finite and report text is escaped',()=>{
  const svg=chartMarkup([{date:'2026-11-01',close:10}],[{key:'close',label:'<img>',color:'#fff'}],'<script>','USD');
  assert.ok(!svg.includes('NaN'));assert.ok(!svg.includes('<script>'));assert.ok(svg.includes('&lt;script&gt;'));assert.ok(!svg.includes('<img>'));
});
