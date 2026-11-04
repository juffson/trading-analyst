const {test}=require('node:test');
const assert=require('node:assert/strict');
const {batch,publicationState,scheduleLabel}=require('../static/daily-close.js');
const index={symbols:[{symbol:'A',market:'CN',tracked:true},{symbol:'B',market:'CN',tracked:true},{symbol:'OLD',market:'CN',tracked:false},{symbol:'C',market:'US',tracked:true}],reports:[
 {symbol:'A',date:'2026-11-04',revision:1,filename:'2026-11-04.json'},
 {symbol:'A',date:'2026-11-04',revision:3,filename:'2026-11-04.v3.json'},
 {symbol:'A',date:'2026-11-03',revision:2,filename:'2026-11-03.v2.json'},
 {symbol:'B',date:'2026-11-03',revision:1},
 {symbol:'OLD',date:'2026-11-04',revision:1},
 {symbol:'C',date:'2026-11-03',revision:1}]};
test('counts each currently tracked symbol once and uses highest revision',()=>{
 const rows=batch(index,'CN','2026-11-04'); assert.equal(rows.length,2);
 assert.equal(rows.filter(r=>r.report).length,1); assert.equal(rows[0].report.revision,3);
 assert.equal(rows[0].previous.revision,2); assert.equal(rows[1].report,null);
 assert.equal(rows[1].latest.date,'2026-11-03');
});
test('historical batches never substitute future reports',()=>{
 const rows=batch(index,'CN','2026-11-02'); assert.ok(rows.every(r=>r.report===null && r.previous===null));
 assert.equal(batch(index,'US','2026-11-03').length,1);
 assert.deepEqual(batch(null,'CN','2026-11-04'),[]);
});
test('workflow success is execution success, not publication verification',()=>{
 assert.deepEqual(publicationState({status:'completed',conclusion:'success'}),['执行通过','ok']);
 assert.deepEqual(publicationState({status:'completed',conclusion:'failure'}),['执行失败','bad']);
 assert.equal(publicationState({status:'in_progress'})[0],'执行中');
 assert.equal(publicationState({status:'completed',conclusion:'skipped'})[0],'已跳过');
 assert.equal(publicationState({status:'other'})[0],'状态待核验');
});
test('schedule display follows config, including custom weekdays',()=>{
 assert.equal(scheduleLabel('RRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH,FR,SA;BYHOUR=7;BYMINUTE=30;BYSECOND=0'),'周二至周六 07:30 · 北京时间');
 assert.equal(scheduleLabel('FREQ=WEEKLY;BYDAY=MO,FR;BYHOUR=16;BYMINUTE=0'),'周一、周五 16:00 · 北京时间');
});
