/* Daily close observer. Report availability is not proof that a job or publication succeeded. */
(function(root) {
  const markets = {CN:'A 股', HK:'港股', US:'美股'};
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = v => typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('zh-CN',{maximumFractionDigits:6}) : '—';
  const stamp = v => v ? new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
  function scheduleLabel(rule) {
    const fields=Object.fromEntries(String(rule || '').replace(/^RRULE:/,'').split(';').map(s=>s.split('=')));
    const names={MO:'一',TU:'二',WE:'三',TH:'四',FR:'五',SA:'六',SU:'日'};
    const days=fields.BYDAY==='MO,TU,WE,TH,FR'?'周一至周五':fields.BYDAY==='TU,WE,TH,FR,SA'?'周二至周六':(fields.BYDAY || '').split(',').map(d=>'周'+(names[d] || '?')).join('、');
    return fields.BYHOUR && fields.BYMINUTE ? `${days} ${fields.BYHOUR.padStart(2,'0')}:${fields.BYMINUTE.padStart(2,'0')} · 北京时间` : '时间待核验';
  }
  function latest(reports) {
    return [...reports].sort((a,b)=>b.date.localeCompare(a.date) || b.revision-a.revision)[0] || null;
  }
  function batch(index, market, date) {
    return (index?.symbols || []).filter(s=>s.tracked && s.market===market).map(symbol=> {
      const reports=(index.reports || []).filter(r=>r.symbol===symbol.symbol);
      return {...symbol, report:latest(reports.filter(r=>r.date===date)), previous:latest(reports.filter(r=>r.date<date)), latest:latest(reports)};
    });
  }
  function publicationState(run) {
    if (run.status==='queued' || run.status==='waiting' || run.status==='pending' || run.status==='requested') return ['排队中','pending'];
    if (run.status==='in_progress') return ['执行中','pending'];
    return ({success:['执行通过','ok'], failure:['执行失败','bad'],cancelled:['已取消','muted'],timed_out:['已超时','bad'],skipped:['已跳过','muted'],action_required:['需处理','bad']})[run.conclusion] || ['状态待核验','muted'];
  }
  const safeRunUrl = url => /^https:\/\/github\.com\/juffson\/trading-analyst\/actions\/runs\/\d+$/.test(url || '') ? url : null;
  function create() {
    let snapshot=null, market='CN', chosenDate='', onlyMissing=false, loading=false, serial=0, lastFetch=0;
    const bodyCache=new Map();
    const $=id=>document.getElementById(id);
    const schedule=m=>(snapshot?.schedules || []).find(s=>s.market===m);
    const defaultDate=m=>schedule(m)?.expected_date || latest((snapshot?.research?.reports || []).filter(r=>snapshot.research.symbols.some(s=>s.symbol===r.symbol && s.market===m && s.tracked)))?.date || '';
    const date=()=>chosenDate || defaultDate(market);
    const body=async report=> {
      const key=`${report.symbol}/${report.filename}`;
      if (!bodyCache.has(key)) {
        const promise=fetch(`/api/github-research/${encodeURIComponent(report.symbol)}/${encodeURIComponent(report.filename)}`).then(async r=>{
          const value=await r.json(); if(!r.ok) throw new Error(value.error || '报告读取失败'); return value;
        });
        bodyCache.set(key,promise);
        promise.catch(()=>bodyCache.delete(key));
      }
      return bodyCache.get(key);
    };
    function renderSchedules() {
      $('dailyMarkets').innerHTML=Object.entries(markets).map(([key,label])=>{
        const s=schedule(key), rows=batch(snapshot.research,key,defaultDate(key));
        const completed=rows.filter(r=>r.report).length;
        return `<button class="dc-market ${key===market?'selected':''}" data-market="${key}" aria-pressed="${key===market}"><div class="dc-between"><strong>${label}收盘</strong><span class="dc-badge ${s?.status==='ACTIVE'?'ok':'muted'}">${s?.status==='ACTIVE'?'计划已启用':s?.status==='PAUSED'?'计划已暂停':'计划待核验'}</span></div><div class="dc-count">${snapshot.research?completed:'—'}<span> / ${snapshot.research?rows.length:'—'} 已归档</span></div><div class="dc-progress"><i style="width:${rows.length?100*completed/rows.length:0}%"></i></div><p>对应交易日 ${esc(defaultDate(key)||'待核验')}</p><p>下次计划 <b>${stamp(s?.next_run)}</b></p><small>${esc(s?.error || scheduleLabel(s?.rrule))}</small></button>`;
      }).join('');
      $('dailyMarkets').querySelectorAll('[data-market]').forEach(b=>b.onclick=()=>{
        market=b.dataset.market; chosenDate=''; renderSchedules(); renderBatch();
      });
    }
    function renderPublications() {
      const pub=snapshot.publications, runs=pub?.runs || [];
      $('dailyPublishState').textContent=snapshot.publication_error || `GitHub Actions · 最近核验 ${stamp(pub?.checked_at)}`;
      $('dailyPublishState').classList.toggle('dc-error',!!snapshot.publication_error);
      $('dailyPublications').innerHTML=runs.length ? runs.map(run=>{
        const [label,tone]=publicationState(run), url=safeRunUrl(run.url);
        return `<div class="dc-run"><span class="dc-badge ${tone}">${label}</span><div><strong>${esc(run.title || '飞书发布流程')}</strong><small>${stamp(run.created_at)} · ${esc(String(run.sha || '').slice(0,7))} · #${esc(run.id)}${pub.stale?' · 缓存记录':''}</small></div>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="查看发布记录 ${esc(run.id)}">查看日志 ↗</a>`:''}</div>`;
      }).join('') : '<div class="dc-empty">暂无可读取的发布执行记录。</div>';
    }
    function cells(row, report, prior, error, previousError) {
      const levels=report?.price_levels?.status==='unavailable'?{}:report?.price_levels || {}, quote=report?.market_snapshot || {};
      const targetChange=typeof levels.target==='number' && prior?.price_levels?.status!=='unavailable' && typeof prior?.price_levels?.target==='number' ? levels.target-prior.price_levels.target : null;
      const detail=report ? `${({Bullish:'偏多',Bearish:'偏空',Neutral:'中性','Strong Bullish':'强看多','Strong Bearish':'强看空'})[report.outlook] || '待判断'} · ${({degraded:'质量受限',blocked:'质量阻断',good:'质量完整'})[report.analysis_quality] || '见报告质量'}` : error?'读取失败，可刷新重试':row.report?'读取关键价格…':'该日尚无归档';
      const status=!row.report?'待核验':error?'读取失败':`已归档 · v${row.report.revision}`;
      const range=typeof levels.entry_low==='number' && typeof levels.entry_high==='number'?`${fmt(levels.entry_low)}–${fmt(levels.entry_high)}`:'—';
      let state=report ? root.ResearchDashboard.levelState(report,new Intl.DateTimeFormat('en-CA',{timeZone:market==='US'?'America/New_York':'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())) : '';
      if(state.startsWith('条件方案')) state='候选方案 · 需复核原报告';
      return `<td><strong>${esc(row.name)}</strong><small>${esc(row.symbol)} · ${['000001.SH','399006.SZ'].includes(row.symbol)?'点':esc(row.currency)}</small></td><td><span class="dc-badge ${error?'bad':row.report?'ok':'muted'}">${status}</span><small>${esc(detail)}</small></td><td>${fmt(quote.close)}<small>${typeof quote.change_pct==='number' ? `${quote.change_pct>0?'+':''}${quote.change_pct.toLocaleString('zh-CN',{maximumFractionDigits:2})}%`:'—'}</small></td><td>${fmt(levels.target)}<small>${previousError?'上期读取失败':targetChange===null?'无可比目标':`较上期 ${targetChange>0?'+':''}${fmt(targetChange)}`}</small></td><td>${range}<small>${esc(state)}</small></td><td>${fmt(levels.stop)}<small>止盈 ${fmt(levels.take_profit_1)} / ${fmt(levels.take_profit_2)}</small></td><td>${row.report?`<button data-daily-report="${esc(row.symbol)}" data-filename="${esc(row.report.filename)}">查看研究</button>`:row.latest?`<button data-daily-report="${esc(row.symbol)}" data-filename="${esc(row.latest.filename)}">最近报告</button><small>${esc(row.latest.date)}</small>`:'—'}</td>`;
    }
    function bindReports() {
      $('dailyRows').querySelectorAll('[data-daily-report]').forEach(b=>b.onclick=()=>root.openDailyResearch(snapshot.research,b.dataset.dailyReport,b.dataset.filename));
    }
    async function renderBatch() {
      const ticket=++serial, selected=date(), rows=batch(snapshot.research,market,selected);
      const dates=[...new Set([defaultDate(market),chosenDate,...(snapshot.research?.reports || []).filter(r=>rows.some(s=>s.symbol===r.symbol)).map(r=>r.date)].filter(Boolean))].sort().reverse();
      $('dailyDate').innerHTML=dates.length?dates.map(d=>`<option ${d===selected?'selected':''}>${esc(d)}</option>`).join(''):'<option value="">暂无日期</option>';
      $('dailyBatchTitle').textContent=`${markets[market]} · ${selected || '暂无报告'}`;
      const completed=rows.filter(r=>r.report).length, s=schedule(market);
      $('dailyBatchMeta').textContent=snapshot.research?`${completed} / ${rows.length} 已归档 · ${rows.length-completed} 个待核验${selected===s?.expected_date && !s.due?' · 尚未到本次计划时间':''}`:'研究目录暂不可用';
      const shown=onlyMissing?rows.filter(r=>!r.report):rows;
      $('dailyRows').innerHTML=shown.map((r,i)=>`<tr data-row="${i}">${cells(r)}</tr>`).join('');
      $('dailyEmpty').hidden=shown.length>0;
      $('dailyEmpty').textContent=!snapshot.research?'未能读取 GitHub 研究目录，请刷新重试。':onlyMissing?'该交易日的当前跟踪标的均已有归档。':'没有当前跟踪标的或可用报告。';
      bindReports();
      // Bound concurrent requests; report identities are immutable revisions.
      let cursor=0;
      await Promise.all(Array.from({length:Math.min(3,shown.length)},async()=>{
        while(cursor<shown.length) {
          const i=cursor++,row=shown[i]; if(!row.report) continue;
          const [current,previous]=await Promise.allSettled([body(row.report),row.previous?body(row.previous):Promise.resolve(null)]);
          if(ticket!==serial) return;
          const tr=$('dailyRows').querySelector(`[data-row="${i}"]`);
          if(tr) tr.innerHTML=cells(row,current.status==='fulfilled'?current.value:null,previous.status==='fulfilled'?previous.value:null,current.status==='rejected',previous.status==='rejected');
          bindReports();
        }
      }));
    }
    async function load(force=false) {
      if(loading || (!force && snapshot && Date.now()-lastFetch<30000)) return;
      loading=true; $('dailyRefresh').disabled=true;
      $('dailyStatus').textContent='正在核对任务计划、研究归档与发布记录…';
      try {
        const response=await fetch('/api/daily-close',{method:force?'POST':'GET',cache:'no-store'});
        if(!response.ok) throw new Error(`HTTP ${response.status}`);
        snapshot=await response.json(); lastFetch=Date.now();
        if(force) bodyCache.clear();
        $('dailyStatus').textContent=[`最近核验 ${stamp(snapshot.checked_at)}（北京时间）`,snapshot.research_error,snapshot.research?.stale?'GitHub 暂不可用，正在展示上次归档，完成度可能已变化':null].filter(Boolean).join(' · ');
        $('dailyStatus').classList.toggle('dc-error',!!snapshot.research_error || !!snapshot.research?.stale);
        renderSchedules(); renderPublications(); await renderBatch();
      } catch(error) {
        $('dailyStatus').textContent=`读取失败：${error.message}。${snapshot?'保留上次结果，状态可能已变化。':'请点击刷新重试。'}`;
        $('dailyStatus').classList.add('dc-error');
      } finally {loading=false; $('dailyRefresh').disabled=false;}
    }
    $('dailyRefresh').onclick=()=>load(true);
    $('dailyDate').onchange=e=>{chosenDate=e.target.value; renderBatch();};
    $('dailyMissing').onchange=e=>{onlyMissing=e.target.checked; renderBatch();};
    setInterval(()=>{if(!document.hidden && $('page-daily-close').classList.contains('active')) load();},60000);
    return {load};
  }
  const api={batch,latest,publicationState,scheduleLabel,create};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  root.DailyClose=api;
})(typeof window==='undefined'?globalThis:window);
