/* Research charts use archived values only. Missing predictions stay missing. */
(function (root) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
  const fmt = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  const pct = value => value == null ? '—' : `${value > 0 ? '+' : ''}${fmt(value)}%`;
  const change = (value, previous) => number(value) && number(previous) ? (value / previous - 1) * 100 : null;
  const file = report => `${report.trade_date}${report.revision > 1 ? `.v${report.revision}` : ''}.json`;
  const outlook = {'Strong Bullish':'强看多', Bullish:'偏多', Neutral:'中性', Bearish:'偏空', 'Strong Bearish':'强看空', Unknown:'未形成判断'};

  function dailyReports(reports, selected) {
    const days = new Map();
    for (const report of reports) {
      if (report.symbol !== selected.symbol || report.trade_date > selected.trade_date) continue;
      const previous = days.get(report.trade_date);
      if (!previous || report.revision > previous.revision) days.set(report.trade_date, report);
    }
    // The selected revision, including v1, must remain the source for its date.
    days.set(selected.trade_date, selected);
    return [...days.values()].sort((a,b) => a.trade_date.localeCompare(b.trade_date));
  }
  function prices(report, history) {
    const days = new Map();
    // Prefer one report's historical series, so adjustment bases are not stitched together.
    for (const point of report.price_history || []) {
      if (/^\d{4}-\d{2}-\d{2}$/.test(point.date) && point.date <= report.trade_date && number(point.close)) days.set(point.date, {date:point.date, close:point.close});
    }
    const fromHistory = days.size > 0;
    if (!fromHistory) for (const r of dailyReports(history, report)) {
      if (number(r.market_snapshot?.close)) days.set(r.trade_date, {date:r.trade_date, close:r.market_snapshot.close});
    }
    if (number(report.market_snapshot?.close)) days.set(report.trade_date, {date:report.trade_date, close:report.market_snapshot.close});
    return {points:[...days.values()].sort((a,b)=>a.date.localeCompare(b.date)), fromHistory};
  }
  function forecasts(reports, selected) {
    return dailyReports(reports, selected).map(report => {
      const scenarios = (report.scenarios || []).filter(s=>number(s.price));
      return {date:report.trade_date, report, close:number(report.market_snapshot?.close),
        target:report.price_levels?.status === 'unavailable' ? null : number(report.price_levels?.target),
        high:scenarios.length ? Math.max(...scenarios.map(s=>s.price)) : null,
        low:scenarios.length ? Math.min(...scenarios.map(s=>s.price)) : null};
    });
  }
  function levelState(report, today) {
    const levels = report.price_levels;
    if (!levels || levels.status === 'unavailable') return '未提供有效买卖方案';
    if (levels.status === 'expired' || (levels.entry_valid_until && levels.entry_valid_until < today)) return '历史方案 · 入场有效期已过';
    return levels.status === 'conditional' ? '条件方案 · 等待触发确认' : '报告标记有效 · 需核对触发条件';
  }
  const series = [
    {key:'close', label:'收盘价', color:'#83c4fb'},
    {key:'target', label:'研究目标价', color:'#b8ee80'},
    {key:'high', label:'情景最高价', color:'#e8bb79', dashed:true},
    {key:'low', label:'情景最低价', color:'#d7919e', dashed:true},
  ];
  function chartMarkup(points, lines, label, unit, width = 760) {
    const values = points.flatMap(p=>lines.map(l=>number(p[l.key]))).filter(v=>v!==null);
    if (!values.length) return '<div class="rd-empty">这段记录没有可绘制的数值</div>';
    const W=Math.max(320, width),H=240,L=60,R=20,T=20,B=34;
    const min=Math.min(...values),max=Math.max(...values),pad=(max-min)*.15 || max*.03 || 1;
    const low=min-pad,high=max+pad;
    const x=i=>L+(points.length===1 ? (W-L-R)/2 : i*(W-L-R)/(points.length-1));
    const y=v=>T+(high-v)*(H-T-B)/(high-low);
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="${esc(label)}，单位 ${esc(unit)}"><title>${esc(label)}</title>`;
    for(let i=0;i<5;i++) {const v=low+(high-low)*i/4; svg+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" class="rd-grid"/><text x="${L-10}" y="${y(v)+4}" text-anchor="end">${fmt(v)}</text>`;}
    const ticks=[...new Set([0,Math.floor((points.length-1)/2),points.length-1])];
    for(const i of ticks) svg+=`<text x="${x(i)}" y="${H-9}" text-anchor="${i===0?'start':i===points.length-1?'end':'middle'}">${esc(points[i].date.slice(5))}</text>`;
    for(const line of lines) {
      let path='', started=false;
      points.forEach((p,i)=>{const v=number(p[line.key]); if(v===null){started=false;return;} path+=`${started?'L':'M'}${x(i)},${y(v)} `; started=true;});
      svg+=`<path d="${path}" fill="none" stroke="${line.color}" stroke-width="2.5" ${line.dashed?'stroke-dasharray="5 4"':''}/>`;
      points.forEach((p,i)=>{if(number(p[line.key])) svg+=`<circle cx="${x(i)}" cy="${y(p[line.key])}" r="${points.length>40?2:3}" fill="${line.color}"/>`;});
    }
    points.forEach((p,i)=>{
      const hitWidth=Math.min(48,(W-L-R)/Math.max(1,points.length-1));
      const text=`${p.date} · ${lines.map(l=>`${l.label} ${fmt(p[l.key])}`).join(' · ')} ${unit}`;
      svg+=`<rect data-point="${i}" x="${x(i)-hitWidth/2}" y="${T}" width="${hitWidth}" height="${H-T-B}" fill="transparent" tabindex="0" role="${p.report?'button':'img'}" aria-label="${esc(text)}${p.report?'，打开该期报告':''}"><title>${esc(text)}</title></rect>`;
    });
    return svg+'</svg>';
  }
  function mountChart(container, points, lines, unit, onSelect) {
    const shown=new Set(lines.map(l=>l.key));
    const plot=container.querySelector('.rd-plot'), tooltip=container.querySelector('.rd-tooltip');
    const describe=p=>{
      tooltip.textContent=`${p.date}${p.report ? ` · v${p.report.revision}`:''} ｜ ${lines.filter(l=>shown.has(l.key)).map(l=>`${l.label} ${fmt(p[l.key])}`).join(' ｜ ')} ${unit}`;
    };
    function draw() {
      plot.innerHTML=chartMarkup(points,lines.filter(l=>shown.has(l.key)),container.dataset.title,unit,plot.clientWidth);
      if(points.length) describe(points[points.length-1]);
      plot.querySelectorAll('[data-point]').forEach(point=>{
        const data=points[Number(point.dataset.point)];
        point.onpointerenter=()=>describe(data);point.onfocus=()=>describe(data);
        if(data.report) {point.onclick=()=>onSelect(file(data.report)); point.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(file(data.report));}};}
      });
    }
    container.querySelectorAll('[data-series]').forEach(button=>{
      button.onclick=()=>{const key=button.dataset.series;if(shown.has(key)){if(shown.size===1)return;shown.delete(key);}else shown.add(key);button.setAttribute('aria-pressed',String(shown.has(key)));draw();};
    });
    draw();
    let lastWidth = plot.clientWidth;
    return new ResizeObserver(() => { if (plot.clientWidth !== lastWidth) { lastWidth = plot.clientWidth; draw(); } });
  }
  function chartCard(id, title, subtitle, lines, unit) {
    return `<section class="rd-chart" id="${id}" data-title="${esc(title)}"><div class="rd-card-heading"><h3>${title}</h3><span>${esc(unit)}</span></div><p class="rd-caption">${subtitle}</p>
      <div class="rd-legend">${lines.map(l=>`<button type="button" data-series="${l.key}" aria-pressed="true"><i style="background:${l.color}"></i>${l.label}</button>`).join('')}</div><div class="rd-plot"></div><div class="rd-tooltip" aria-live="polite"></div></section>`;
  }
  function render(container, report, history, options={}) {
    for (const observer of container.researchObservers || []) observer.disconnect();
    const {unit='', today=new Date().toISOString().slice(0,10), onSelect=()=>{}, loading=false, failed=0}=options;
    const price=prices(report,history), forecast=forecasts(history,report);
    const snapshot=report.market_snapshot || {}, levels=report.price_levels || {};
    const previous=forecast.length>1 ? forecast[forecast.length-2] : null;
    const current=forecast[forecast.length-1];
    const delta=previous && number(current.target) && number(previous.target) ? current.target-previous.target : null;
    const spanChange=price.points.length>1 ? change(price.points.at(-1).close,price.points[0].close) : null;
    const stat=(title,value,note,cls='')=>`<div class="rd-stat"><span>${title}</span><strong class="${cls}">${value}</strong><small>${note}</small></div>`;
    const canShow=levels.status!=='unavailable';
    const level=k=>canShow ? number(levels[k]) : null;
    const levelCard=(label,value,note,cls)=>`<div class="rd-level ${cls}"><span>${label}</span><strong>${value}</strong><small>${esc(note)}</small></div>`;
    const range=level('entry_low') && level('entry_high') ? `${fmt(level('entry_low'))}–${fmt(level('entry_high'))}` : '未提供';
    container.innerHTML=`<div class="rd-summary"><div class="rd-eyebrow">${esc(report.trade_date)} · v${report.revision} <span>${esc(outlook[report.outlook]||report.outlook)}</span><span>资料${esc({pass:'通过',degraded:'受限',blocked:'不足'}[report.analysis_quality]||'未评级')}</span></div><h2>${esc(report.headline)}</h2><p>${esc(report.summary)}</p></div>
      <div class="rd-stats">
      ${stat('报告收盘价',`${fmt(number(snapshot.close))} <em>${esc(unit)}</em>`,`单日 ${pct(snapshot.change_pct)}`,snapshot.change_pct>0?'rd-positive':snapshot.change_pct<0?'rd-negative':'')}
      ${stat('区间价格变化',pct(spanChange),price.points.length ? `${price.points[0].date} → ${price.points.at(-1).date}`:'缺少历史行情',spanChange>0?'rd-positive':spanChange<0?'rd-negative':'')}
      ${stat('本期研究目标价',current.target ? fmt(current.target) : '未提供',delta!==null ? `较上期 ${delta>0?'+':''}${fmt(delta)} ${esc(unit)}（${pct(change(current.target,previous.target))}）`:'仅比较连续两期均有目标价的记录')}
      ${stat('本期情景价格',current.low ? `${fmt(current.low)}–${fmt(current.high)}`:'未提供','报告情景的最低与最高价')}
      </div>
      <div class="rd-charts">
      ${chartCard('rdPrice','价格走势',price.fromHistory ? `所选报告附带的 ${price.points.length} 个交易日收盘价`:'报告日收盘快照；无连续行情的日期未补值',[series[0]],unit)}
      ${chartCard('rdForecast','预测价格如何变化',`横轴为报告交易日 · 同日取最高修订，所选日使用 v${report.revision}`,series,unit)}
      </div>
      <div class="rd-caption rd-history-state">${loading?'正在补齐历史预测…':failed?`${failed} 期读取失败，图中保留空档，可刷新 GitHub 重试。`:`已显示截至所选日期的 ${forecast.length} 期研究。`} 情景区间不代表概率区间；各期预测期限可能不同。缺失预测不连线。点击预测图的数据点可查看原报告。</div>
      <section class="rd-trades"><div class="rd-card-heading"><h3>关键买卖点位</h3><span class="rd-status">${esc(levelState(report,today))}</span></div>
      <p class="rd-caption">以下为 ${esc(report.trade_date)} 报告中的条件价位 · ${esc(unit)} · 入场有效期 ${esc(levels.entry_valid_until||'未提供')}</p>
      <div class="rd-levels">${levelCard('买入 / 入场区间',range,'以报告触发条件为准','rd-entry')}${levelCard('止损 / 失效线',level('stop')?fmt(level('stop')):'未提供','结构失效后需重新研究','rd-stop')}${levelCard('第一止盈',level('take_profit_1')?fmt(level('take_profit_1')):'未提供','报告原始第一目标','rd-target')}${levelCard('第二止盈',level('take_profit_2')?fmt(level('take_profit_2')):'未提供','报告原始第二目标','rd-target')}${levelCard('研究目标价',level('target')?fmt(level('target')):'未提供',levels.target_horizon||'未给出目标期限','rd-target')}</div>
      <div class="rd-conditions"><p><b>触发条件</b>${esc(levels.trigger||'报告未提供触发条件')}</p><p><b>失效条件</b>${esc(levels.invalidation||'报告未提供失效条件')}</p></div>
      ${levels.basis?`<p class="rd-caption">价位依据：${esc(levels.basis)}</p>`:''}</section>
      ${(report.scenarios||[]).length?`<section class="rd-scenarios"><h3>本期情景与条件</h3><div>${report.scenarios.map(s=>`<article><span>${esc(s.name)}</span><strong>${fmt(number(s.price))} <em>${esc(unit)}</em></strong><p>${esc(s.condition)}</p><small>${esc(s.horizon||'未给出期限')}</small></article>`).join('')}</div></section>`:''}
      <details class="rd-history"><summary>查看逐期预测与修订来源（${forecast.length} 期）</summary><p class="rd-caption">同日取最高修订；所选日期遵循所选版本。补录报告按交易日排列，实际生成时间单独列出。</p><div class="rd-table-scroll"><table><thead><tr><th>报告 / 生成时间</th><th>收盘</th><th>目标价</th><th>情景范围</th><th>目标期限 / 状态</th></tr></thead><tbody>${[...forecast].reverse().map(p=>`<tr><td><button data-open-report="${file(p.report)}">${esc(p.date)} · v${p.report.revision}</button><small>${esc(p.report.generated_at||'未读取')}</small></td><td>${fmt(p.close)}</td><td>${fmt(p.target)}</td><td>${p.low?`${fmt(p.low)}–${fmt(p.high)}`:'—'}</td><td>${esc(p.report.price_levels?.target_horizon||'未提供目标期限')}<small>${esc(levelState(p.report,today))}</small></td></tr>`).join('')}</tbody></table></div></details>`;
    container.researchObservers = [
      mountChart(container.querySelector('#rdPrice'),price.points,[series[0]],unit,onSelect),
      mountChart(container.querySelector('#rdForecast'),forecast,series,unit,onSelect),
    ];
    container.researchObservers.forEach((observer, i) => observer.observe(container.querySelectorAll('.rd-plot')[i]));
    container.querySelectorAll('[data-open-report]').forEach(button=>button.onclick=()=>onSelect(button.dataset.openReport));
  }
  const api={dailyReports,prices,forecasts,levelState,chartMarkup,render};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.ResearchDashboard=api;
})(typeof window!=='undefined'?window:globalThis);
