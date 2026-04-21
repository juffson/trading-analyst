#!/usr/bin/env python3
"""
render_dashboard.py
生成深度价值投资分析的 HTML 决策仪表盘。

用法：
    python render_dashboard.py \
        --data-dir /path/to/workspace/company_data/茅台 \
        --analysis-json /path/to/analysis_summary.json \
        --output /path/to/茅台_价值分析_YYYY-MM-DD.html

analysis_summary.json 的结构由主 Claude 生成，结构见 README 或 SKILL.md。
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path


def load_json(path):
    """尝试读取 JSON 文件，失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def safe_get(d, *keys, default=None):
    """安全取嵌套字典值。"""
    cur = d
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list):
            try:
                cur = cur[k]
            except (IndexError, TypeError):
                return default
        else:
            return default
    return cur if cur is not None else default


def format_number(n, unit="亿", decimals=2):
    """格式化数字。"""
    if n is None:
        return "—"
    try:
        n = float(n)
        return f"{n:.{decimals}f}{unit}"
    except (ValueError, TypeError):
        return str(n)


def score_to_color(score):
    """分数 → 颜色。"""
    if score is None:
        return "#9CA3AF"
    if score >= 4:
        return "#16A34A"
    if score >= 3:
        return "#EAB308"
    if score >= 2:
        return "#F97316"
    return "#DC2626"


def recommendation_band(total_score, max_score=55):
    """根据总分给出推荐档位。"""
    ratio = total_score / max_score
    if ratio >= 45 / 55:
        return ("强烈推荐", "#16A34A", "基本面优异，估值合理，建议深度研究")
    if ratio >= 40 / 55:
        return ("优质公司-等买点", "#84CC16", "基本面优秀，等更好买点")
    if ratio >= 35 / 55:
        return ("中性跟踪", "#EAB308", "综合评分中等，继续观察")
    if ratio >= 30 / 55:
        return ("暂不考虑", "#F97316", "基本面或估值有瑕疵")
    return ("避免", "#DC2626", "建议回避")


def render_dashboard(data_dir, analysis, output_path):
    """主渲染函数。"""
    # 读取基础数据
    stock_quote = load_json(os.path.join(data_dir, "stock_quote.json")) or {}
    financials = load_json(os.path.join(data_dir, "financial_statements.json")) or {}

    # analysis 是主 Claude 传进来的综合分析 JSON
    scores = analysis.get("scores", {})  # 11 个维度
    valuation = analysis.get("valuation", {})  # DCF + 账面资产
    moat = analysis.get("moat", {})  # 护城河分析
    pestel = analysis.get("pestel", {})  # PESTEL 六维
    porter = analysis.get("porter", {})  # 波特六力
    second_level = analysis.get("second_level_thinking", {})
    fatal_risks = analysis.get("fatal_risks", [])
    action_plan = analysis.get("action_plan", {})

    # 计算总分
    dim_keys = [
        "business_simplicity", "economic_model", "business_model",
        "culture", "moat", "management", "pestel", "porter_forces",
        "margin_of_safety", "second_level_thinking", "fatal_risks"
    ]
    dim_labels = [
        "业务简单性", "经济模型", "商业模式", "企业文化",
        "护城河", "管理团队", "PESTEL", "波特五力",
        "安全边际", "第二层思维", "致命风险"
    ]
    dim_scores = []
    for k in dim_keys:
        s = safe_get(scores, k, "score", default=0)
        try:
            dim_scores.append(float(s))
        except (ValueError, TypeError):
            dim_scores.append(0)
    total_score = sum(dim_scores)

    band_name, band_color, band_desc = recommendation_band(total_score)

    # 基本信息
    company_name = stock_quote.get("name") or analysis.get("company_name", "未知公司")
    code = stock_quote.get("code") or analysis.get("code", "")
    exchange = stock_quote.get("exchange", "")
    currency = stock_quote.get("currency", "CNY")
    currency_symbol = {"CNY": "¥", "HKD": "HK$", "USD": "$"}.get(currency, "")

    price = stock_quote.get("price", "—")
    change_pct = stock_quote.get("change_pct", 0)
    change_color = "#DC2626" if (isinstance(change_pct, (int, float)) and change_pct >= 0) else "#16A34A"
    change_sign = "+" if (isinstance(change_pct, (int, float)) and change_pct >= 0) else ""

    pe = stock_quote.get("pe_ttm", "—")
    pb = stock_quote.get("pb", "—")
    market_cap = stock_quote.get("market_cap_total", "—")
    dividend_yield = stock_quote.get("dividend_yield_ttm", "—")

    report_date = datetime.now().strftime("%Y-%m-%d")

    # 5 年趋势图数据
    years = financials.get("years", [])
    roe_hist = safe_get(financials, "core_ratios", "roe", default=[])
    revenue_hist = safe_get(financials, "income_statement", "revenue", default=[])
    net_profit_hist = safe_get(financials, "income_statement", "net_profit", default=[])
    gross_margin_hist = safe_get(financials, "core_ratios", "gross_margin", default=[])
    net_margin_hist = safe_get(financials, "core_ratios", "net_margin", default=[])
    fcf_hist = safe_get(financials, "cash_flow_statement", "free_cash_flow", default=[])
    debt_ratio_hist = safe_get(financials, "core_ratios", "debt_to_assets", default=[])

    # DCF 三档估值
    bear_v = safe_get(valuation, "scenarios", "bear", "per_share_value", default=0)
    base_v = safe_get(valuation, "scenarios", "base", "per_share_value", default=0)
    bull_v = safe_get(valuation, "scenarios", "bull", "per_share_value", default=0)
    asset_per_share = safe_get(valuation, "asset_per_share", default=0)

    try:
        price_num = float(price) if price not in (None, "—") else 0
    except (ValueError, TypeError):
        price_num = 0

    margin_pct = 0
    if base_v:
        try:
            margin_pct = (float(base_v) - price_num) / float(base_v) * 100
        except (ValueError, TypeError, ZeroDivisionError):
            margin_pct = 0

    # PESTEL & Porter 数据
    pestel_scores = [
        safe_get(pestel, "political", "score", default=3),
        safe_get(pestel, "economic", "score", default=3),
        safe_get(pestel, "social", "score", default=3),
        safe_get(pestel, "technology", "score", default=3),
        safe_get(pestel, "environment", "score", default=3),
        safe_get(pestel, "legal", "score", default=3),
    ]
    pestel_trends = [
        safe_get(pestel, "political", "trend", default="→"),
        safe_get(pestel, "economic", "trend", default="→"),
        safe_get(pestel, "social", "trend", default="→"),
        safe_get(pestel, "technology", "trend", default="→"),
        safe_get(pestel, "environment", "trend", default="→"),
        safe_get(pestel, "legal", "trend", default="→"),
    ]

    porter_scores = [
        safe_get(porter, "existing_competition", "score", default=3),
        safe_get(porter, "new_entrants", "score", default=3),
        safe_get(porter, "substitutes", "score", default=3),
        safe_get(porter, "supplier_power", "score", default=3),
        safe_get(porter, "buyer_power", "score", default=3),
        safe_get(porter, "complements", "score", default=3),
    ]

    # 数据源
    data_sources = stock_quote.get("data_sources", []) + financials.get("data_sources", [])
    data_sources_str = " / ".join(set(data_sources)) or "多源聚合"
    as_of_date = stock_quote.get("as_of_date") or financials.get("as_of_date") or report_date

    # 生成 HTML
    html = _build_html(
        company_name=company_name,
        code=code,
        exchange=exchange,
        currency_symbol=currency_symbol,
        price=price,
        change_pct=change_pct,
        change_color=change_color,
        change_sign=change_sign,
        pe=pe,
        pb=pb,
        market_cap=market_cap,
        dividend_yield=dividend_yield,
        report_date=report_date,
        total_score=total_score,
        band_name=band_name,
        band_color=band_color,
        band_desc=band_desc,
        one_liner=analysis.get("one_liner", ""),
        dim_labels=dim_labels,
        dim_scores=dim_scores,
        scores=scores,
        years=years,
        roe_hist=roe_hist,
        revenue_hist=revenue_hist,
        net_profit_hist=net_profit_hist,
        gross_margin_hist=gross_margin_hist,
        net_margin_hist=net_margin_hist,
        fcf_hist=fcf_hist,
        debt_ratio_hist=debt_ratio_hist,
        bear_v=bear_v,
        base_v=base_v,
        bull_v=bull_v,
        asset_per_share=asset_per_share,
        price_num=price_num,
        margin_pct=margin_pct,
        moat=moat,
        pestel_scores=pestel_scores,
        pestel_trends=pestel_trends,
        porter_scores=porter_scores,
        pestel=pestel,
        porter=porter,
        second_level=second_level,
        fatal_risks=fatal_risks,
        action_plan=action_plan,
        data_sources_str=data_sources_str,
        as_of_date=as_of_date,
    )

    # 写出
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ 仪表盘已生成：{output_path}")
    return output_path


def _build_html(**ctx):
    """把所有数据拼成 HTML 字符串。为了可读性拆成若干小模板段。"""
    # 把需要注入到 JavaScript 的数据 JSON 化
    dim_scores_json = json.dumps(ctx["dim_scores"])
    dim_labels_json = json.dumps(ctx["dim_labels"], ensure_ascii=False)
    years_json = json.dumps(ctx["years"])
    roe_json = json.dumps(ctx["roe_hist"])
    revenue_json = json.dumps(ctx["revenue_hist"])
    net_profit_json = json.dumps(ctx["net_profit_hist"])
    gross_margin_json = json.dumps(ctx["gross_margin_hist"])
    net_margin_json = json.dumps(ctx["net_margin_hist"])
    fcf_json = json.dumps(ctx["fcf_hist"])
    debt_ratio_json = json.dumps(ctx["debt_ratio_hist"])
    bear_json = json.dumps(ctx["bear_v"])
    base_json = json.dumps(ctx["base_v"])
    bull_json = json.dumps(ctx["bull_v"])
    asset_json = json.dumps(ctx["asset_per_share"])
    price_json = json.dumps(ctx["price_num"])
    pestel_scores_json = json.dumps(ctx["pestel_scores"])
    porter_scores_json = json.dumps(ctx["porter_scores"])

    # 11 维度评分卡
    dim_cards = ""
    for label, key in zip(ctx["dim_labels"], [
        "business_simplicity", "economic_model", "business_model",
        "culture", "moat", "management", "pestel", "porter_forces",
        "margin_of_safety", "second_level_thinking", "fatal_risks"
    ]):
        s = safe_get(ctx["scores"], key, "score", default=0)
        reason = safe_get(ctx["scores"], key, "reason", default="—")
        try:
            s_float = float(s)
        except (ValueError, TypeError):
            s_float = 0
        color = score_to_color(s_float)
        dim_cards += f'''
        <div class="dim-card">
            <div class="dim-header">
                <span class="dim-label">{label}</span>
                <span class="dim-score" style="color:{color};">{s}/5</span>
            </div>
            <div class="dim-reason">{reason}</div>
        </div>
        '''

    # 致命风险列表
    risks_html = ""
    for i, r in enumerate(ctx["fatal_risks"][:5], 1):
        risk_name = r.get("risk", "—") if isinstance(r, dict) else str(r)
        prob = r.get("probability", "—") if isinstance(r, dict) else "—"
        impact = r.get("impact", "—") if isinstance(r, dict) else "—"
        response = r.get("management_response", "—") if isinstance(r, dict) else "—"
        risks_html += f'''
        <div class="risk-card">
            <div class="risk-header">
                <span class="risk-num">{i}</span>
                <span class="risk-name">{risk_name}</span>
            </div>
            <div class="risk-body">
                <div><strong>概率</strong>: {prob}</div>
                <div><strong>影响</strong>: {impact}</div>
                <div><strong>管理层应对</strong>: {response}</div>
            </div>
        </div>
        '''

    # 行动计划
    buy_price = ctx["action_plan"].get("buy_price_range", "—")
    position_size = ctx["action_plan"].get("position_size", "—")
    track_metrics = ctx["action_plan"].get("track_metrics", [])
    exit_signals = ctx["action_plan"].get("exit_signals", [])
    track_list = "".join([f"<li>{m}</li>" for m in track_metrics]) or "<li>—</li>"
    exit_list = "".join([f"<li>{s}</li>" for s in exit_signals]) or "<li>—</li>"

    # 第二层思维
    market_view = ctx["second_level"].get("market_view", "—")
    our_view = ctx["second_level"].get("our_view", "—")
    catalysts = ctx["second_level"].get("catalysts", [])
    verifications = ctx["second_level"].get("verifications_needed", [])
    cat_list = "".join([f"<li>{c}</li>" for c in catalysts]) or "<li>—</li>"
    ver_list = "".join([f"<li>{v}</li>" for v in verifications]) or "<li>—</li>"

    # PESTEL 趋势标签
    pestel_labels = ["政治 P", "经济 E", "社会 S", "技术 T", "环境 En", "法律 L"]
    pestel_tags = ""
    for lab, s, t in zip(pestel_labels, ctx["pestel_scores"], ctx["pestel_trends"]):
        color = score_to_color(float(s) if s else 3)
        pestel_tags += f'<div class="pestel-tag" style="border-color:{color};"><span class="pestel-label">{lab}</span><span class="pestel-score">{s}/5</span><span class="pestel-trend">{t}</span></div>'

    # 主 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{ctx["company_name"]} 深度价值分析</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "PingFang SC", "Helvetica Neue", "Microsoft YaHei", Arial, sans-serif;
         background: #F9FAFB; color: #111827; line-height: 1.6; padding: 24px; }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  .header {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 16px;
             box-shadow: 0 1px 3px rgba(0,0,0,0.04); display: flex; justify-content: space-between; align-items: center; }}
  .header-left h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
  .header-left .sub {{ color: #6B7280; font-size: 13px; }}
  .header-right {{ text-align: right; }}
  .price {{ font-size: 28px; font-weight: 600; }}
  .change {{ font-size: 14px; }}
  .banner {{ background: {ctx["band_color"]}; color: white; border-radius: 12px; padding: 24px;
             margin-bottom: 16px; text-align: center; }}
  .banner .label {{ font-size: 14px; opacity: 0.9; margin-bottom: 4px; }}
  .banner .score {{ font-size: 42px; font-weight: 600; margin-bottom: 8px; }}
  .banner .rec {{ font-size: 18px; font-weight: 500; margin-bottom: 4px; }}
  .banner .desc {{ font-size: 13px; opacity: 0.9; }}

  .section {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 16px;
              box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .section h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; padding-bottom: 8px;
                 border-bottom: 2px solid #E5E7EB; }}

  .metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .metric {{ background: #F3F4F6; border-radius: 8px; padding: 12px; text-align: center; }}
  .metric .k {{ font-size: 11px; color: #6B7280; margin-bottom: 4px; }}
  .metric .v {{ font-size: 18px; font-weight: 600; font-family: 'JetBrains Mono', 'SF Mono', monospace; }}

  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
  .chart-wrap {{ position: relative; height: 280px; }}

  .dim-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
  .dim-card {{ background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px; }}
  .dim-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
  .dim-label {{ font-size: 13px; font-weight: 500; }}
  .dim-score {{ font-size: 16px; font-weight: 600; }}
  .dim-reason {{ font-size: 11px; color: #6B7280; line-height: 1.5; }}

  .valuation-table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  .valuation-table th, .valuation-table td {{ padding: 8px 12px; border-bottom: 1px solid #E5E7EB; text-align: right; font-size: 13px; }}
  .valuation-table th {{ background: #F3F4F6; font-weight: 500; color: #6B7280; }}
  .valuation-table td:first-child, .valuation-table th:first-child {{ text-align: left; }}

  .margin-bar {{ position: relative; height: 36px; background: linear-gradient(to right, #DC2626 0%, #EAB308 50%, #16A34A 100%);
                 border-radius: 8px; margin: 16px 0; }}
  .margin-marker {{ position: absolute; top: -6px; width: 4px; height: 48px; background: #111827; }}
  .margin-label {{ position: absolute; top: -28px; padding: 2px 6px; background: #111827; color: white;
                   font-size: 11px; border-radius: 4px; transform: translateX(-50%); }}

  .pestel-tags {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px; }}
  .pestel-tag {{ border: 2px solid; border-radius: 8px; padding: 8px 12px; text-align: center; }}
  .pestel-label {{ font-size: 12px; color: #6B7280; display: block; }}
  .pestel-score {{ font-size: 16px; font-weight: 600; margin: 0 4px; }}
  .pestel-trend {{ font-size: 16px; }}

  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .col-card {{ background: #F9FAFB; border-radius: 8px; padding: 16px; }}
  .col-card h3 {{ font-size: 13px; color: #6B7280; margin-bottom: 8px; }}
  .col-card .content {{ font-size: 14px; line-height: 1.7; }}

  .risk-card {{ background: #FEF2F2; border-left: 4px solid #DC2626; border-radius: 6px;
                padding: 12px; margin-bottom: 8px; }}
  .risk-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .risk-num {{ background: #DC2626; color: white; width: 22px; height: 22px; border-radius: 50%;
               display: inline-flex; align-items: center; justify-content: center; font-size: 12px; }}
  .risk-name {{ font-weight: 500; font-size: 14px; }}
  .risk-body {{ font-size: 12px; color: #6B7280; display: grid; gap: 2px; padding-left: 30px; }}

  .plan-box {{ background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 8px; padding: 16px; }}
  .plan-box h3 {{ font-size: 13px; color: #16A34A; margin-bottom: 8px; }}
  .plan-row {{ margin-bottom: 12px; }}
  .plan-row strong {{ display: inline-block; min-width: 100px; font-size: 13px; }}
  .plan-row ul {{ margin-left: 20px; font-size: 13px; }}

  .footer {{ text-align: center; padding: 24px; color: #9CA3AF; font-size: 12px; line-height: 1.8; }}
  .footer hr {{ border: none; border-top: 1px solid #E5E7EB; margin: 12px 0; }}

  @media (max-width: 768px) {{
    .grid-2, .grid-3, .metrics-grid, .dim-grid, .two-col {{ grid-template-columns: 1fr; }}
    .header {{ flex-direction: column; align-items: flex-start; }}
    .header-right {{ text-align: left; margin-top: 12px; }}
  }}
</style>
</head>
<body>
<div class="container">

  <!-- 1. 标题 -->
  <div class="header">
    <div class="header-left">
      <h1>{ctx["company_name"]} <span style="color:#6B7280;font-weight:400;">({ctx["code"]} · {ctx["exchange"]})</span></h1>
      <div class="sub">深度价值分析报告 · 生成日期 {ctx["report_date"]}</div>
    </div>
    <div class="header-right">
      <div class="price">{ctx["currency_symbol"]}{ctx["price"]}</div>
      <div class="change" style="color:{ctx["change_color"]};">{ctx["change_sign"]}{ctx["change_pct"]}%</div>
    </div>
  </div>

  <!-- 2. 综合评分横幅 -->
  <div class="banner">
    <div class="label">11 维度综合评分</div>
    <div class="score">{ctx["total_score"]:.1f} / 55</div>
    <div class="rec">{ctx["band_name"]}</div>
    <div class="desc">{ctx["one_liner"] or ctx["band_desc"]}</div>
  </div>

  <!-- 3. 关键指标速览 -->
  <div class="section">
    <h2>关键指标速览</h2>
    <div class="metrics-grid">
      <div class="metric"><div class="k">总市值</div><div class="v">{ctx["market_cap"]}</div></div>
      <div class="metric"><div class="k">PE (TTM)</div><div class="v">{ctx["pe"]}</div></div>
      <div class="metric"><div class="k">PB</div><div class="v">{ctx["pb"]}</div></div>
      <div class="metric"><div class="k">股息率 TTM</div><div class="v">{ctx["dividend_yield"]}%</div></div>
    </div>
  </div>

  <!-- 4. 雷达图 + 11 维度卡片 -->
  <div class="section">
    <h2>11 维度雷达图与评分</h2>
    <div class="grid-2">
      <div class="chart-wrap"><canvas id="radarChart"></canvas></div>
      <div class="dim-grid">{dim_cards}</div>
    </div>
  </div>

  <!-- 5. 财务五年趋势 -->
  <div class="section">
    <h2>财务五年趋势</h2>
    <div class="grid-3">
      <div class="chart-wrap"><canvas id="roeChart"></canvas></div>
      <div class="chart-wrap"><canvas id="revenueChart"></canvas></div>
      <div class="chart-wrap"><canvas id="profitChart"></canvas></div>
      <div class="chart-wrap"><canvas id="marginChart"></canvas></div>
      <div class="chart-wrap"><canvas id="fcfChart"></canvas></div>
      <div class="chart-wrap"><canvas id="debtChart"></canvas></div>
    </div>
  </div>

  <!-- 6. 三档 DCF 估值 + 账面资产 -->
  <div class="section">
    <h2>DCF 估值三档情景</h2>
    <div class="chart-wrap"><canvas id="valuationChart"></canvas></div>
    <table class="valuation-table">
      <tr><th>情景</th><th>DCF 每股价值</th><th>账面资产每股</th><th>合计每股总价值</th><th>相对当前价</th></tr>
      <tr>
        <td>乐观</td>
        <td>{ctx["currency_symbol"]}{ctx["bull_v"]}</td>
        <td>{ctx["currency_symbol"]}{ctx["asset_per_share"]}</td>
        <td>{ctx["currency_symbol"]}{(float(ctx["bull_v"] or 0) + float(ctx["asset_per_share"] or 0)):.2f}</td>
        <td>{(((float(ctx["bull_v"] or 0) + float(ctx["asset_per_share"] or 0)) / ctx["price_num"] - 1) * 100 if ctx["price_num"] else 0):.1f}%</td>
      </tr>
      <tr>
        <td>中性</td>
        <td>{ctx["currency_symbol"]}{ctx["base_v"]}</td>
        <td>{ctx["currency_symbol"]}{ctx["asset_per_share"]}</td>
        <td>{ctx["currency_symbol"]}{(float(ctx["base_v"] or 0) + float(ctx["asset_per_share"] or 0)):.2f}</td>
        <td>{(((float(ctx["base_v"] or 0) + float(ctx["asset_per_share"] or 0)) / ctx["price_num"] - 1) * 100 if ctx["price_num"] else 0):.1f}%</td>
      </tr>
      <tr>
        <td>悲观</td>
        <td>{ctx["currency_symbol"]}{ctx["bear_v"]}</td>
        <td>{ctx["currency_symbol"]}{ctx["asset_per_share"]}</td>
        <td>{ctx["currency_symbol"]}{(float(ctx["bear_v"] or 0) + float(ctx["asset_per_share"] or 0)):.2f}</td>
        <td>{(((float(ctx["bear_v"] or 0) + float(ctx["asset_per_share"] or 0)) / ctx["price_num"] - 1) * 100 if ctx["price_num"] else 0):.1f}%</td>
      </tr>
    </table>
  </div>

  <!-- 7. 安全边际 -->
  <div class="section">
    <h2>安全边际</h2>
    <p style="font-size:13px;color:#6B7280;margin-bottom:8px;">当前股价 vs 中性情景估值</p>
    <div class="margin-bar">
      <!-- 标记 (股价在 bear..bull 间的位置) -->
    </div>
    <p style="font-size:14px;">中性每股价值约 <strong>{ctx["currency_symbol"]}{ctx["base_v"]}</strong>，
       当前股价 <strong>{ctx["currency_symbol"]}{ctx["price"]}</strong>，
       安全边际 <strong style="color:{'#16A34A' if ctx["margin_pct"] > 0 else '#DC2626'};">
       {ctx["margin_pct"]:+.1f}%</strong></p>
  </div>

  <!-- 8. PESTEL -->
  <div class="section">
    <h2>PESTEL 宏观环境</h2>
    <div class="grid-2">
      <div class="chart-wrap"><canvas id="pestelChart"></canvas></div>
      <div class="pestel-tags">{pestel_tags}</div>
    </div>
  </div>

  <!-- 9. 波特六力 -->
  <div class="section">
    <h2>动态波特六力</h2>
    <div class="chart-wrap" style="max-width:500px;margin:0 auto;"><canvas id="porterChart"></canvas></div>
  </div>

  <!-- 10. 第二层思维 -->
  <div class="section">
    <h2>第二层思维</h2>
    <div class="two-col">
      <div class="col-card">
        <h3>市场当前观点</h3>
        <div class="content">{market_view}</div>
      </div>
      <div class="col-card" style="background:#EFF6FF;border-left:4px solid #1E40AF;">
        <h3>我们的观点</h3>
        <div class="content">{our_view}</div>
      </div>
    </div>
    <div class="two-col" style="margin-top:12px;">
      <div class="col-card">
        <h3>催化剂</h3>
        <ul style="margin-left:20px;font-size:13px;">{cat_list}</ul>
      </div>
      <div class="col-card">
        <h3>需要验证的指标</h3>
        <ul style="margin-left:20px;font-size:13px;">{ver_list}</ul>
      </div>
    </div>
  </div>

  <!-- 11. 致命风险 -->
  <div class="section">
    <h2>致命风险清单（Top {len(ctx["fatal_risks"][:5])})</h2>
    {risks_html}
  </div>

  <!-- 12. 行动计划 -->
  <div class="section">
    <h2>投资决策与行动计划</h2>
    <div class="plan-box">
      <div class="plan-row"><strong>建议买入价</strong>{buy_price}</div>
      <div class="plan-row"><strong>建议仓位</strong>{position_size}</div>
      <div class="plan-row"><strong>跟踪指标</strong><ul>{track_list}</ul></div>
      <div class="plan-row"><strong>退出信号</strong><ul>{exit_list}</ul></div>
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <hr>
    <p>本报告由 AI 公司价值分析专家生成</p>
    <p>数据来源：{ctx["data_sources_str"]}　·　数据时点：{ctx["as_of_date"]}</p>
    <p>分析框架：价值投资 11 维度 + DCF 估值</p>
    <p style="color:#DC2626;margin-top:8px;"><strong>免责声明</strong>：本报告仅供学习研究，不构成任何投资建议。投资有风险，入市需谨慎，请独立判断并自负盈亏。</p>
  </div>
</div>

<script>
  // Chart.js 配置
  const commonOpts = {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: true, position: 'bottom', labels: {{ font: {{ size: 11 }} }} }} }}
  }};

  // 1. 雷达图
  new Chart(document.getElementById('radarChart'), {{
    type: 'radar',
    data: {{
      labels: {dim_labels_json},
      datasets: [{{
        label: '评分',
        data: {dim_scores_json},
        backgroundColor: 'rgba(30,64,175,0.15)',
        borderColor: '#1E40AF', borderWidth: 2,
        pointBackgroundColor: '#1E40AF'
      }}]
    }},
    options: {{
      ...commonOpts,
      scales: {{ r: {{ min: 0, max: 5, ticks: {{ stepSize: 1, font: {{ size: 10 }} }} }} }}
    }}
  }});

  const years = {years_json};

  // 2. ROE
  new Chart(document.getElementById('roeChart'), {{
    type: 'line',
    data: {{ labels: years, datasets: [{{ label: 'ROE %', data: {roe_json}, borderColor: '#1E40AF', backgroundColor: 'rgba(30,64,175,0.1)', fill: true, tension: 0.3 }}] }},
    options: commonOpts
  }});

  // 3. 营收
  new Chart(document.getElementById('revenueChart'), {{
    type: 'line',
    data: {{ labels: years, datasets: [{{ label: '营收 (亿)', data: {revenue_json}, borderColor: '#16A34A', backgroundColor: 'rgba(22,163,74,0.1)', fill: true, tension: 0.3 }}] }},
    options: commonOpts
  }});

  // 4. 净利润
  new Chart(document.getElementById('profitChart'), {{
    type: 'line',
    data: {{ labels: years, datasets: [{{ label: '净利润 (亿)', data: {net_profit_json}, borderColor: '#EAB308', backgroundColor: 'rgba(234,179,8,0.1)', fill: true, tension: 0.3 }}] }},
    options: commonOpts
  }});

  // 5. 毛利率 / 净利率
  new Chart(document.getElementById('marginChart'), {{
    type: 'line',
    data: {{ labels: years, datasets: [
      {{ label: '毛利率 %', data: {gross_margin_json}, borderColor: '#0EA5E9', tension: 0.3 }},
      {{ label: '净利率 %', data: {net_margin_json}, borderColor: '#DC2626', tension: 0.3 }}
    ] }},
    options: commonOpts
  }});

  // 6. FCF
  new Chart(document.getElementById('fcfChart'), {{
    type: 'bar',
    data: {{ labels: years, datasets: [{{ label: '自由现金流 (亿)', data: {fcf_json}, backgroundColor: '#8B5CF6' }}] }},
    options: commonOpts
  }});

  // 7. 负债率
  new Chart(document.getElementById('debtChart'), {{
    type: 'line',
    data: {{ labels: years, datasets: [{{ label: '资产负债率 %', data: {debt_ratio_json}, borderColor: '#F97316', backgroundColor: 'rgba(249,115,22,0.1)', fill: true, tension: 0.3 }}] }},
    options: commonOpts
  }});

  // 8. 估值三档 bar
  new Chart(document.getElementById('valuationChart'), {{
    type: 'bar',
    data: {{
      labels: ['悲观', '中性', '乐观'],
      datasets: [
        {{ label: 'DCF 每股价值', data: [{bear_json}, {base_json}, {bull_json}], backgroundColor: ['#DC2626', '#EAB308', '#16A34A'] }}
      ]
    }},
    options: {{
      ...commonOpts, indexAxis: 'y',
      scales: {{ x: {{ beginAtZero: true }} }},
      plugins: {{
        legend: {{ display: false }},
        annotation: {{ annotations: {{ line: {{ type: 'line', xMin: {price_json}, xMax: {price_json}, borderColor: '#111827', borderWidth: 2, borderDash: [5,5], label: {{ content: '当前股价', enabled: true }} }} }} }}
      }}
    }}
  }});

  // 9. PESTEL 雷达
  new Chart(document.getElementById('pestelChart'), {{
    type: 'radar',
    data: {{
      labels: ['政治', '经济', '社会', '技术', '环境', '法律'],
      datasets: [{{
        label: 'PESTEL 评分',
        data: {pestel_scores_json},
        backgroundColor: 'rgba(22,163,74,0.15)',
        borderColor: '#16A34A', borderWidth: 2
      }}]
    }},
    options: {{ ...commonOpts, scales: {{ r: {{ min: 0, max: 5 }} }} }}
  }});

  // 10. 波特六力 雷达
  new Chart(document.getElementById('porterChart'), {{
    type: 'radar',
    data: {{
      labels: ['现有竞争', '新进入者', '替代品', '供应商', '买方', '互补品'],
      datasets: [{{
        label: '波特六力评分',
        data: {porter_scores_json},
        backgroundColor: 'rgba(234,179,8,0.15)',
        borderColor: '#EAB308', borderWidth: 2
      }}]
    }},
    options: {{ ...commonOpts, scales: {{ r: {{ min: 0, max: 5 }} }} }}
  }});
</script>
</body>
</html>
"""
    return html


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True, help="公司数据目录（包含 stock_quote.json, financial_statements.json 等）")
    p.add_argument("--analysis-json", required=True, help="主代理生成的综合分析 JSON")
    p.add_argument("--output", required=True, help="输出 HTML 路径")
    args = p.parse_args()

    with open(args.analysis_json, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    render_dashboard(args.data_dir, analysis, args.output)


if __name__ == "__main__":
    main()
