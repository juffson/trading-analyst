#!/usr/bin/env python3
"""
plan_io.py — trading plan 与复盘结果的本地存储工具

所有输入从 stdin 读 JSON，输出到 stdout。路径由调用方（skill）指定。
不做路径假设，不写默认目录 —— 由 skill 在调用前问用户。

子命令:
  save-plan        存计划到 <dir>/plan_<YYYY-MM-DD>.json（+ 简版 HTML sidecar）
  save-review      存复盘到 <dir>/review_<YYYY-MM-DD>.json（+ 简版 HTML sidecar）
  load-latest-plan 加载目录下最新的 plan_*.json
  list-plans       列出目录下所有 plan 文件（按日期倒序）
  diff-snapshot    输入 {plan, current_snapshot} 输出对比结构

用法:
  echo '<plan_json>' | python3 plan_io.py save-plan --dir <abs_dir>
  python3 plan_io.py load-latest-plan --dir <abs_dir>
  python3 plan_io.py list-plans --dir <abs_dir>
  echo '{"plan": {...}, "current_snapshot": {...}}' | python3 plan_io.py diff-snapshot

所有成功输出的 JSON 都带 `ok: true` 字段；失败带 `ok: false` 和 `error` 字段。
"""

import argparse
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "1"
PLAN_FILE_RE = re.compile(r"^plan_(\d{4}-\d{2}-\d{2})\.json$")
REVIEW_FILE_RE = re.compile(r"^review_(\d{4}-\d{2}-\d{2})\.json$")


def read_stdin_json():
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    return json.loads(raw)


def print_result(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def fail(error, **extra):
    out = {"ok": False, "error": error}
    out.update(extra)
    print_result(out)
    sys.exit(1)


def ensure_dir(path):
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def validate_plan(plan):
    errors = []
    required_top = ["symbol", "plan_date", "snapshot", "price_levels", "t_plans", "scenarios"]
    for k in required_top:
        if k not in plan:
            errors.append(f"缺少顶层字段: {k}")

    if "plan_date" in plan and not re.match(r"^\d{4}-\d{2}-\d{2}$", plan["plan_date"]):
        errors.append("plan_date 格式必须是 YYYY-MM-DD")

    if "scenarios" in plan:
        if not isinstance(plan["scenarios"], list) or len(plan["scenarios"]) < 2:
            errors.append("scenarios 至少 2 个")
        else:
            prob_sum = sum(s.get("probability", 0) for s in plan["scenarios"])
            if abs(prob_sum - 1.0) > 0.05:
                errors.append(f"scenarios 概率和 {prob_sum:.2f} 偏离 1.0 超过 0.05")

    if "t_plans" in plan:
        for i, t in enumerate(plan["t_plans"]):
            for k in ["direction", "trigger_price", "shares"]:
                if k not in t:
                    errors.append(f"t_plans[{i}] 缺少 {k}")
            if "direction" in t and t["direction"] not in ("forward_T", "reverse_T"):
                errors.append(f"t_plans[{i}].direction 必须是 forward_T 或 reverse_T")

    if "price_levels" in plan:
        for i, lvl in enumerate(plan["price_levels"]):
            for k in ["price", "type", "meaning"]:
                if k not in lvl:
                    errors.append(f"price_levels[{i}] 缺少 {k}")

    return errors


def validate_review(review):
    errors = []
    required = ["symbol", "review_date", "prior_plan_path", "current_snapshot"]
    for k in required:
        if k not in review:
            errors.append(f"缺少顶层字段: {k}")
    return errors


# -------------------- HTML sidecar rendering --------------------

_HTML_STYLE = """
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #0f1419; color: #d4d4d4; padding: 24px; max-width: 1000px; margin: 0 auto; }
  h1, h2, h3 { color: #e8e8e8; border-bottom: 1px solid #2a3038; padding-bottom: 4px; }
  h1 { color: #7dd3fc; }
  .meta { color: #94a3b8; font-size: 13px; margin-bottom: 16px; }
  .card { background: #1a1f26; border-radius: 6px; padding: 16px; margin: 12px 0; }
  .kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; }
  .kv dt { color: #94a3b8; }
  .kv dd { margin: 0; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #2a3038; }
  th { background: #252b33; color: #cbd5e1; }
  .resistance { color: #f87171; }
  .support { color: #4ade80; }
  .pnl-neg { color: #f87171; }
  .pnl-pos { color: #4ade80; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px;
         background: #2a3038; color: #cbd5e1; }
  .footer { color: #64748b; font-size: 12px; margin-top: 32px; border-top: 1px solid #2a3038; padding-top: 12px; }
  code { background: #2a3038; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
</style>
"""


def esc(v):
    if v is None:
        return ""
    return html_lib.escape(str(v))


def render_plan_html(plan):
    sym = esc(plan.get("symbol"))
    date = esc(plan.get("plan_date"))
    snap = plan.get("snapshot", {})

    pnl_pct = snap.get("unrealized_pnl_pct")
    pnl_class = "pnl-pos" if (pnl_pct or 0) >= 0 else "pnl-neg"

    parts = [f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>交易计划 {sym} {date}</title>{_HTML_STYLE}</head><body>
<h1>交易计划 · {sym}</h1>
<div class="meta">plan_date: {date} · version: {esc(plan.get('version', SCHEMA_VERSION))} · created_at: {esc(plan.get('created_at', ''))}</div>

<div class="card"><h2>持仓快照</h2><dl class="kv">
<dt>现价</dt><dd>{esc(snap.get('price'))}</dd>
<dt>持股</dt><dd>{esc(snap.get('shares'))}</dd>
<dt>成本</dt><dd>{esc(snap.get('cost_basis'))}</dd>
<dt>市值</dt><dd>{esc(snap.get('market_value'))}</dd>
<dt>浮盈亏</dt><dd class="{pnl_class}">{esc(snap.get('unrealized_pnl'))} ({esc(pnl_pct)}%)</dd>
</dl></div>"""]

    if plan.get("position_split"):
        ps = plan["position_split"]
        parts.append(f"""<div class="card"><h2>仓位划分</h2><dl class="kv">
<dt>底仓</dt><dd>{esc(ps.get('base_shares'))} 股</dd>
<dt>活动仓</dt><dd>{esc(ps.get('active_shares'))} 股</dd>
<dt>逻辑</dt><dd>{esc(ps.get('rationale'))}</dd>
</dl></div>""")

    if plan.get("price_levels"):
        rows = []
        for lvl in sorted(plan["price_levels"], key=lambda x: -x.get("price", 0)):
            cls = esc(lvl.get("type", ""))
            rows.append(f"<tr><td class='{cls}'>{esc(lvl.get('price'))}</td>"
                        f"<td class='{cls}'>{esc(lvl.get('type'))}</td>"
                        f"<td>{esc(lvl.get('meaning'))}</td>"
                        f"<td>{esc(lvl.get('action'))}</td></tr>")
        parts.append(f"""<div class="card"><h2>关键价位</h2>
<table><tr><th>价格</th><th>类型</th><th>技术含义</th><th>动作</th></tr>
{''.join(rows)}</table></div>""")

    if plan.get("t_plans"):
        rows = []
        for t in plan["t_plans"]:
            rows.append(f"<tr><td><span class='tag'>{esc(t.get('direction'))}</span></td>"
                        f"<td>{esc(t.get('trigger_price'))}</td>"
                        f"<td>{esc(t.get('trigger_condition'))}</td>"
                        f"<td>{esc(t.get('shares'))}</td>"
                        f"<td>{esc(t.get('exit_trigger_price'))}</td>"
                        f"<td>{esc(t.get('exit_condition'))}</td>"
                        f"<td>{esc(t.get('logic'))}</td></tr>")
        parts.append(f"""<div class="card"><h2>做 T 计划</h2>
<table><tr><th>方向</th><th>触发价</th><th>触发条件</th><th>股数</th>
<th>退出价</th><th>退出条件</th><th>逻辑</th></tr>
{''.join(rows)}</table></div>""")

    if plan.get("scenarios"):
        rows = []
        for s in plan["scenarios"]:
            tr = s.get("target_range", ["", ""])
            rows.append(f"<tr><td>{esc(s.get('name'))}</td>"
                        f"<td>{esc(int((s.get('probability', 0) * 100)))}%</td>"
                        f"<td>{esc(s.get('trigger'))}</td>"
                        f"<td>{esc(s.get('path'))}</td>"
                        f"<td>{esc(tr[0])} - {esc(tr[1])}</td>"
                        f"<td>{esc(s.get('recommended_strategy'))}</td></tr>")
        parts.append(f"""<div class="card"><h2>情景预估</h2>
<table><tr><th>情景</th><th>概率</th><th>触发</th><th>路径</th><th>目标区间</th><th>策略</th></tr>
{''.join(rows)}</table></div>""")

    if plan.get("discipline"):
        items = "".join(f"<li>{esc(d)}</li>" for d in plan["discipline"])
        parts.append(f"""<div class="card"><h2>操作纪律</h2><ul>{items}</ul></div>""")

    parts.append(f"""<div class="footer">本计划为 plan_io.py 生成的简版 sidecar，完整分析见主 HTML 报告。
数据仅供参考，不构成投资建议。</div></body></html>""")

    return "".join(parts)


def render_review_html(review):
    sym = esc(review.get("symbol"))
    date = esc(review.get("review_date"))

    parts = [f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>复盘 {sym} {date}</title>{_HTML_STYLE}</head><body>
<h1>复盘报告 · {sym}</h1>
<div class="meta">review_date: {date} · 基于计划: {esc(review.get('prior_plan_path'))} · 间隔: {esc(review.get('days_elapsed'))} 天</div>"""]

    sv = review.get("scenario_verdict") or {}
    if sv:
        parts.append(f"""<div class="card"><h2>情景验证</h2><dl class="kv">
<dt>实际匹配情景</dt><dd><strong>{esc(sv.get('actual_scenario_match'))}</strong></dd>
<dt>原先概率</dt><dd>{esc(json.dumps(sv.get('prior_probabilities', {}), ensure_ascii=False))}</dd>
<dt>评价</dt><dd>{esc(sv.get('accuracy_comment'))}</dd>
</dl></div>""")

    if review.get("price_level_checks"):
        rows = []
        for c in review["price_level_checks"]:
            hit = "✓" if c.get("hit") else "✗"
            held = "" if not c.get("hit") else ("守住" if c.get("held") else "失守")
            rows.append(f"<tr><td>{esc(c.get('price'))}</td>"
                        f"<td class='{esc(c.get('type'))}'>{esc(c.get('type'))}</td>"
                        f"<td>{hit}</td><td>{held}</td>"
                        f"<td>{esc(c.get('first_touch_date', ''))}</td>"
                        f"<td>{esc(c.get('comment'))}</td></tr>")
        parts.append(f"""<div class="card"><h2>关键价位验证</h2>
<table><tr><th>价格</th><th>类型</th><th>触及</th><th>状态</th><th>首次触及</th><th>说明</th></tr>
{''.join(rows)}</table></div>""")

    if review.get("t_plan_execution"):
        rows = []
        for t in review["t_plan_execution"]:
            exec_tag = "已执行" if t.get("executed") else "未触发"
            pnl = t.get("pnl")
            pnl_cls = "pnl-pos" if (pnl or 0) >= 0 else "pnl-neg"
            rows.append(f"<tr><td>{esc(t.get('direction'))}</td>"
                        f"<td>{esc(t.get('planned_trigger_price'))}</td>"
                        f"<td>{esc(t.get('planned_shares'))}</td>"
                        f"<td>{exec_tag}</td>"
                        f"<td>{esc(t.get('executed_price', ''))}</td>"
                        f"<td class='{pnl_cls}'>{esc(pnl)}</td>"
                        f"<td>{esc(t.get('discipline_comment', ''))}</td></tr>")
        parts.append(f"""<div class="card"><h2>做 T 执行情况</h2>
<table><tr><th>方向</th><th>计划价</th><th>计划股</th><th>执行</th><th>成交价</th><th>盈亏</th><th>纪律</th></tr>
{''.join(rows)}</table></div>""")

    cc = review.get("cost_change") or {}
    if cc:
        parts.append(f"""<div class="card"><h2>成本变化</h2><dl class="kv">
<dt>计划前成本</dt><dd>{esc(cc.get('prior_cost_basis'))}</dd>
<dt>当前成本</dt><dd>{esc(cc.get('current_cost_basis'))}</dd>
<dt>实际降幅</dt><dd>{esc(cc.get('actual_reduction_pct'))}%</dd>
<dt>对应预期</dt><dd>{esc(cc.get('plan_normal_estimate_pct'))}%</dd>
<dt>评价</dt><dd>{esc(cc.get('verdict'))}</dd>
</dl></div>""")

    if review.get("discipline_breaches"):
        items = "".join(f"<li class='pnl-neg'>{esc(b)}</li>" for b in review["discipline_breaches"])
        parts.append(f"""<div class="card"><h2>纪律破例</h2><ul>{items}</ul></div>""")

    if review.get("lessons"):
        items = "".join(f"<li>{esc(l)}</li>" for l in review["lessons"])
        parts.append(f"""<div class="card"><h2>经验教训</h2><ul>{items}</ul></div>""")

    if review.get("updated_plan_path"):
        parts.append(f"""<div class="card"><h2>更新后的计划</h2>
<p>路径: <code>{esc(review['updated_plan_path'])}</code></p></div>""")

    parts.append(f"""<div class="footer">由 plan_io.py 生成。</div></body></html>""")
    return "".join(parts)


# -------------------- subcommands --------------------

def cmd_save_plan(args):
    plan = read_stdin_json()
    if plan is None:
        fail("stdin 没读到 JSON")
    plan.setdefault("version", SCHEMA_VERSION)
    plan.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))

    errors = validate_plan(plan)
    if errors and not args.force:
        fail("plan 校验失败", validation_errors=errors)

    out_dir = ensure_dir(args.dir)
    date = plan["plan_date"]
    json_path = out_dir / f"plan_{date}.json"
    html_path = out_dir / f"plan_{date}.html"

    overwritten = json_path.exists()
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_plan_html(plan), encoding="utf-8")

    print_result({
        "ok": True,
        "json_path": str(json_path),
        "html_path": str(html_path),
        "overwritten": overwritten,
        "validation_errors": errors,
    })


def cmd_save_review(args):
    review = read_stdin_json()
    if review is None:
        fail("stdin 没读到 JSON")
    review.setdefault("version", SCHEMA_VERSION)
    review.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))

    errors = validate_review(review)
    if errors and not args.force:
        fail("review 校验失败", validation_errors=errors)

    out_dir = ensure_dir(args.dir)
    date = review["review_date"]
    json_path = out_dir / f"review_{date}.json"
    html_path = out_dir / f"review_{date}.html"

    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_review_html(review), encoding="utf-8")

    print_result({
        "ok": True,
        "json_path": str(json_path),
        "html_path": str(html_path),
        "validation_errors": errors,
    })


def _list_plan_files(directory):
    d = Path(directory).expanduser().resolve()
    if not d.is_dir():
        return []
    items = []
    for p in d.iterdir():
        m = PLAN_FILE_RE.match(p.name)
        if m:
            items.append({"date": m.group(1), "path": str(p)})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items


def cmd_list_plans(args):
    items = _list_plan_files(args.dir)
    print_result({"ok": True, "plans": items, "count": len(items)})


def cmd_load_latest_plan(args):
    items = _list_plan_files(args.dir)
    if not items:
        print_result({"ok": True, "found": False, "plan": None})
        return
    latest = items[0]
    try:
        data = json.loads(Path(latest["path"]).read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"读取 {latest['path']} 失败: {e}")
        return
    print_result({
        "ok": True,
        "found": True,
        "path": latest["path"],
        "date": latest["date"],
        "plan": data,
    })


def cmd_diff_snapshot(args):
    """
    输入: {"plan": <plan>, "current_snapshot": {"price": ..., "high_since": ..., "low_since": ...,
            "cost_basis": ..., "shares": ..., "as_of": "YYYY-MM-DD"}}
    输出: 价位是否触及 / 情景候选 / 成本变化
    """
    data = read_stdin_json()
    if not data:
        fail("stdin 没读到 JSON")
    plan = data.get("plan") or {}
    cur = data.get("current_snapshot") or {}
    if not plan or not cur:
        fail("需要同时提供 plan 和 current_snapshot")

    price = cur.get("price")
    high_since = cur.get("high_since", price)
    low_since = cur.get("low_since", price)

    level_checks = []
    for lvl in plan.get("price_levels", []):
        lp = lvl.get("price")
        t = lvl.get("type")
        hit = False
        held = None
        if lp is None or price is None:
            pass
        elif t == "resistance":
            hit = high_since is not None and high_since >= lp
            held = hit and (price < lp)
        elif t == "support":
            hit = low_since is not None and low_since <= lp
            held = hit and (price > lp)
        level_checks.append({
            "price": lp, "type": t, "meaning": lvl.get("meaning"),
            "hit": bool(hit),
            "held": held,
        })

    scenario_candidates = []
    for s in plan.get("scenarios", []):
        tr = s.get("target_range")
        match = False
        if tr and len(tr) == 2 and price is not None:
            lo, hi = sorted(tr)
            match = lo <= price <= hi
        scenario_candidates.append({
            "name": s.get("name"),
            "probability": s.get("probability"),
            "target_range": tr,
            "price_in_range": match,
        })

    cost_change = None
    prior_cost = (plan.get("snapshot") or {}).get("cost_basis")
    cur_cost = cur.get("cost_basis")
    if prior_cost is not None and cur_cost is not None and prior_cost:
        cost_change = {
            "prior_cost_basis": prior_cost,
            "current_cost_basis": cur_cost,
            "actual_reduction_pct": round((prior_cost - cur_cost) / prior_cost * 100, 3),
        }

    print_result({
        "ok": True,
        "symbol": plan.get("symbol"),
        "plan_date": plan.get("plan_date"),
        "as_of": cur.get("as_of"),
        "price_level_checks": level_checks,
        "scenario_candidates": scenario_candidates,
        "cost_change": cost_change,
    })


# -------------------- main --------------------

def main():
    parser = argparse.ArgumentParser(description="交易计划与复盘的 JSON 存储工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_save = sub.add_parser("save-plan", help="存计划到 <dir>/plan_<date>.json")
    p_save.add_argument("--dir", required=True, help="存放目录（绝对路径）")
    p_save.add_argument("--force", action="store_true", help="即使校验失败也存")
    p_save.set_defaults(func=cmd_save_plan)

    p_review = sub.add_parser("save-review", help="存复盘到 <dir>/review_<date>.json")
    p_review.add_argument("--dir", required=True)
    p_review.add_argument("--force", action="store_true")
    p_review.set_defaults(func=cmd_save_review)

    p_list = sub.add_parser("list-plans", help="列出目录下所有 plan 文件")
    p_list.add_argument("--dir", required=True)
    p_list.set_defaults(func=cmd_list_plans)

    p_load = sub.add_parser("load-latest-plan", help="加载最新的 plan.json")
    p_load.add_argument("--dir", required=True)
    p_load.set_defaults(func=cmd_load_latest_plan)

    p_diff = sub.add_parser("diff-snapshot", help="计划 vs 当前行情对比")
    p_diff.set_defaults(func=cmd_diff_snapshot)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
