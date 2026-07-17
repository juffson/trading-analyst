#!/usr/bin/env python3
"""生成本地监控 dashboard（auto-trader/dashboard.html，深色主题，不提交/不分享）。

配色遵循 dataviz skill 的方法：状态色（pending/rejected/executed）用它的固定 status
palette（never themed），阶段徽章（researching→...→promoted_live）当成"有序阶段"用单一
蓝色 ramp 由浅到深编码进度，不是分类色板——因为这四个阶段有严格的先后顺序，不是并列身份。

跟 company-deep-dive 的 render_dashboard.py 一样走本地 HTML 文件 + Chart.js CDN 的路子，
不用 Artifact 工具发布——这里全是个人持仓/交易数据，不应该产出一个 claude.ai 的托管链接。
"""
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_store  # noqa: E402
import state_store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "strategies.json"
WATCHLIST_DIR = REPO_ROOT / "watchlist"
OUT_PATH = REPO_ROOT / "dashboard.html"

# ── 配色（来自 dataviz skill 的 palette.md，dark 主题）──────────────────────────
STATUS_COLOR = {"pending": "#fab219", "rejected": "#d03b3b", "executed": "#0ca30c"}
STAGE_ORDER = ["researching", "calibrating", "paper_trading", "promoted_live"]
STAGE_COLOR = {  # 有序阶段用同一蓝色 ramp 由浅到深，越深代表进展越靠后
    "researching": "#86b6ef", "calibrating": "#5598e7",
    "paper_trading": "#2a78d6", "promoted_live": "#184f95",
}
INK_PRIMARY, INK_SECONDARY, INK_MUTED = "#ffffff", "#c3c2b7", "#898781"
SURFACE, PAGE_PLANE, GRIDLINE = "#1a1a19", "#0d0d0d", "#2c2c2a"


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _load_jsonl(path, limit=None):
    if not path.exists():
        return []
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines[-limit:] if limit else lines


def _esc(v):
    return html.escape(str(v)) if v is not None else "—"


def _fmt_num(v, digits=2, suffix=""):
    return f"{v:.{digits}f}{suffix}" if isinstance(v, (int, float)) else "—"


def _fmt_ts(ms):
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def gather_symbol_data(symbol, cfg):
    symbol_dir = WATCHLIST_DIR / symbol
    stage = _load_json(symbol_dir / "stage.json") or {"stage": "researching", "history": []}
    backtest = _load_json(symbol_dir / "backtest_report.json") or {}
    plans = sorted(plan_store.load_latest_plans(symbol).values(), key=lambda p: p.get("created_at", ""), reverse=True)
    trades = _load_jsonl(symbol_dir / "trade_log.jsonl", limit=20)[::-1]
    state = state_store.load(symbol)
    return {
        "symbol": symbol, "cfg": cfg, "stage": stage, "backtest": backtest,
        "plans": plans, "trades": trades, "state": state,
    }


def status_counts(all_plans):
    counts = {"pending": 0, "rejected": 0, "executed": 0}
    for plans in all_plans:
        for p in plans:
            counts[p.get("status", "pending")] = counts.get(p.get("status", "pending"), 0) + 1
    return counts


def render_stage_badge(stage):
    color = STAGE_COLOR.get(stage, INK_MUTED)
    return f'<span class="badge" style="background:{color};">{_esc(stage)}</span>'


def render_status_badge(status):
    color = STATUS_COLOR.get(status, INK_MUTED)
    return f'<span class="badge status-{_esc(status)}" style="background:{color};">{_esc(status)}</span>'


def render_overview_row(data):
    b = data["backtest"]
    pending = sum(1 for p in data["plans"] if p.get("status") == "pending")
    last_signal = data["plans"][0]["created_at"] if data["plans"] else None
    return f"""
    <tr>
      <td><strong>{_esc(data['symbol'])}</strong></td>
      <td>{render_stage_badge(data['stage'].get('stage'))}</td>
      <td>{_esc(data['cfg'].get('execution_mode', 'signal_only'))}</td>
      <td>{_fmt_num(b.get('sharpe_ratio'))}</td>
      <td>{_fmt_num(b.get('max_drawdown_pct'), suffix='%')}</td>
      <td>{_fmt_num(b.get('win_rate_pct'), suffix='%')}</td>
      <td>{pending}</td>
      <td>{_esc(last_signal[:16].replace('T', ' ') if last_signal else '—')}</td>
    </tr>"""


def render_symbol_section(data):
    symbol = data["symbol"]
    plans_rows = "".join(f"""
      <tr>
        <td>{render_status_badge(p.get('status'))}</td>
        <td>{_esc(p.get('side'))}</td>
        <td>{_esc(p.get('qty'))}</td>
        <td>{_esc(p.get('price'))}</td>
        <td>{_esc((p.get('created_at') or '')[:16].replace('T', ' '))}</td>
        <td>{_esc(p.get('note') or p.get('summary'))}</td>
      </tr>""" for p in data["plans"][:15]) or '<tr><td colspan="6" class="muted">暂无交易计划</td></tr>'

    trade_rows = "".join(f"""
      <tr>
        <td>{_esc((t.get('ts') or '')[:16].replace('T', ' '))}</td>
        <td>{_esc(t.get('side'))}</td>
        <td>{_esc(t.get('qty'))}</td>
        <td>{_esc(t.get('price'))}</td>
      </tr>""" for t in data["trades"]) or '<tr><td colspan="4" class="muted">暂无模拟成交记录</td></tr>'

    history_items = "".join(
        f'<li><span class="muted">{_esc((h.get("entered_at") or "")[:16].replace("T", " "))}</span> '
        f'→ {render_stage_badge(h.get("stage"))} <span class="muted">{_esc(h.get("reason"))}</span></li>'
        for h in reversed(data["stage"].get("history", []))
    ) or '<li class="muted">暂无阶段流转记录</li>'

    positions = data["state"].get("positions", {})
    ready = data["stage"].get("ready_for_live_promotion")

    return f"""
    <section class="card">
      <h2>{_esc(symbol)} {render_stage_badge(data['stage'].get('stage'))}
        {'<span class="badge" style="background:#0ca30c;">ready_for_live_promotion</span>' if ready else ''}
      </h2>
      <div class="grid-2">
        <div>
          <h3>当前持仓</h3>
          <p class="muted">shares: {_esc(positions.get('shares', 0))} · cost_basis: {_esc(positions.get('cost_basis', 0))}</p>
          <h3>阶段流转历史</h3>
          <ul class="history">{history_items}</ul>
        </div>
        <div>
          <h3>回测基线</h3>
          <p class="muted">strategy_file: {_esc(data['cfg'].get('strategy_file'))}</p>
          <p class="muted">sharpe={_fmt_num(data['backtest'].get('sharpe_ratio'))} ·
             max_drawdown={_fmt_num(data['backtest'].get('max_drawdown_pct'), suffix='%')} ·
             win_rate={_fmt_num(data['backtest'].get('win_rate_pct'), suffix='%')} ·
             trades={_esc(data['backtest'].get('total_trades'))}</p>
        </div>
      </div>
      <h3>交易计划（最近 15 条）</h3>
      <table class="data-table">
        <thead><tr><th>状态</th><th>方向</th><th>数量</th><th>价格</th><th>生成时间</th><th>备注</th></tr></thead>
        <tbody>{plans_rows}</tbody>
      </table>
      <h3>模拟成交记录（最近 20 条）</h3>
      <table class="data-table">
        <thead><tr><th>时间</th><th>方向</th><th>数量</th><th>价格</th></tr></thead>
        <tbody>{trade_rows}</tbody>
      </table>
    </section>"""


def render(symbols_data):
    overview_rows = "".join(render_overview_row(d) for d in symbols_data) or \
        '<tr><td colspan="8" class="muted">config/strategies.json 里还没有配置标的</td></tr>'
    sections = "".join(render_symbol_section(d) for d in symbols_data)
    counts = status_counts([d["plans"] for d in symbols_data])
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<title>Auto Trader Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{
    --surface: {SURFACE}; --page: {PAGE_PLANE}; --ink: {INK_PRIMARY};
    --ink-sec: {INK_SECONDARY}; --ink-muted: {INK_MUTED}; --grid: {GRIDLINE};
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--page);
          color: var(--ink); margin: 0; padding: 24px; line-height: 1.6; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 0 0 16px; display: flex; align-items: center; gap: 8px; }}
  h3 {{ font-size: 13px; color: var(--ink-sec); margin: 16px 0 8px; text-transform: uppercase; letter-spacing: 0.03em; }}
  .muted {{ color: var(--ink-muted); font-size: 13px; }}
  .sub {{ color: var(--ink-sec); font-size: 13px; margin-bottom: 24px; }}
  .card {{ background: var(--surface); border: 1px solid var(--grid); border-radius: 12px;
           padding: 20px; margin-bottom: 20px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 11px;
            font-weight: 600; color: #0b0b0b; }}
  table.data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  table.data-table th {{ text-align: left; color: var(--ink-muted); font-weight: 500;
                          padding: 6px 10px; border-bottom: 1px solid var(--grid); font-size: 11px;
                          text-transform: uppercase; }}
  table.data-table td {{ padding: 8px 10px; border-bottom: 1px solid var(--grid); }}
  ul.history {{ list-style: none; padding: 0; margin: 0; font-size: 13px; }}
  ul.history li {{ padding: 4px 0; border-bottom: 1px solid var(--grid); }}
  .chart-wrap {{ max-width: 280px; }}
  .overview-wrap {{ display: grid; grid-template-columns: 2fr 1fr; gap: 24px; align-items: start; }}
  @media (max-width: 900px) {{ .grid-2, .overview-wrap {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
  <h1>Auto Trader Dashboard</h1>
  <div class="sub">生成时间: {generated_at} · 本地文件，不提交/不分享</div>

  <section class="card overview-wrap">
    <div>
      <h3>标的总览</h3>
      <table class="data-table">
        <thead><tr>
          <th>标的</th><th>阶段</th><th>execution_mode</th><th>Sharpe</th>
          <th>Max DD</th><th>Win Rate</th><th>待处理计划</th><th>最近信号</th>
        </tr></thead>
        <tbody>{overview_rows}</tbody>
      </table>
    </div>
    <div>
      <h3>交易计划状态分布</h3>
      <div class="chart-wrap"><canvas id="statusChart"></canvas></div>
    </div>
  </section>

  {sections}

<script>
new Chart(document.getElementById('statusChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['pending', 'rejected', 'executed'],
    datasets: [{{
      data: [{counts['pending']}, {counts['rejected']}, {counts['executed']}],
      backgroundColor: ['{STATUS_COLOR["pending"]}', '{STATUS_COLOR["rejected"]}', '{STATUS_COLOR["executed"]}'],
      borderColor: '{SURFACE}', borderWidth: 2,
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '{INK_SECONDARY}', padding: 12 }} }} }}
  }}
}});
</script>
</body></html>"""


def main():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {"symbols": {}}
    symbols = cfg.get("symbols") or {}
    symbols_data = [gather_symbol_data(sym, sym_cfg) for sym, sym_cfg in symbols.items() if sym_cfg]

    OUT_PATH.write_text(render(symbols_data), encoding="utf-8")
    print(f"已生成: {OUT_PATH}")


if __name__ == "__main__":
    main()
