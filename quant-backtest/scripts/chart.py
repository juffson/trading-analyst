#!/usr/bin/env python3
"""
终端绘制 quant-run 返回的 chart_json 和 report_json 曲线。
依赖 plotext：pip3 install plotext

用法：
  # 绘制 chart_json 中所有 plot 线条
  python3 chart.py <response.json>

  # 只绘制指定标题的 plot
  python3 chart.py <response.json> --plots "MACD,Signal"

  # 绘制权益曲线 + 买入持有对照
  python3 chart.py <response.json> --equity

  # 绘制回撤曲线
  python3 chart.py <response.json> --drawdown

  # 全部绘制
  python3 chart.py <response.json> --all

  # 控制图表高度（默认 15 行）
  python3 chart.py <response.json> --all --height 20

输入文件为 RunScript API 的完整响应 JSON。
"""

import json
import sys
import argparse

try:
    import plotext as plt
except ImportError:
    print("需要安装 plotext: pip3 install plotext")
    sys.exit(1)

COLORS = ["blue", "red", "green", "magenta", "cyan", "yellow", "orange", "white"]
DEFAULT_HEIGHT = 15


def load_response(path):
    with open(path) as f:
        resp = json.load(f)
    data = resp.get("data", resp)
    report = json.loads(data["report_json"]) if data.get("report_json") else {}
    chart = json.loads(data["chart_json"]) if data.get("chart_json") else {}
    return report, chart


def setup_plot(title, height):
    plt.clear_figure()
    plt.theme("dark")
    plt.title(title)
    plt.plot_size(None, height)


def plot_series(chart, filter_titles=None, height=DEFAULT_HEIGHT):
    """绘制 chart_json 中的 plot 线条"""
    sg = chart.get("series_graphs", {})
    if not sg:
        print("chart_json 中没有 series_graphs 数据")
        return

    plots = []
    for idx in sorted(sg.keys(), key=int):
        entry = sg[idx]
        plot_type = list(entry.keys())[0]
        inner = entry[plot_type]
        title = inner.get("title", f"plot-{idx}")
        series = inner.get("series", [])
        style = inner.get("style", "Line")
        if filter_titles and title not in filter_titles:
            continue
        if series:
            plots.append((title, series, style))

    if not plots:
        print("没有匹配的 plot 数据")
        return

    lines = [(t, s, st) for t, s, st in plots if st != "Histogram"]
    histograms = [(t, s, st) for t, s, st in plots if st == "Histogram"]

    if lines:
        setup_plot("Chart - Lines", height)
        for i, (title, series, _) in enumerate(lines):
            plt.plot(series, label=title, color=COLORS[i % len(COLORS)])
        plt.show()
        print()

    for i, (title, series, _) in enumerate(histograms):
        setup_plot(f"Chart - {title}", height)
        plt.bar(range(len(series)), series, color=COLORS[(len(lines) + i) % len(COLORS)], width=0.3)
        plt.show()
        print()


def plot_equity(report, height=DEFAULT_HEIGHT):
    """绘制权益曲线 + 买入持有对照"""
    equity = report.get("equityCurve", [])
    buyhold = report.get("buyHoldCurve", [])
    if not equity:
        print("report_json 中没有 equityCurve 数据")
        return

    setup_plot("Equity vs Buy & Hold", height)
    plt.plot(equity, label="Strategy", color="green")
    if buyhold:
        plt.plot(buyhold, label="Buy & Hold", color="gray")
    plt.show()
    print()


def plot_drawdown(report, height=DEFAULT_HEIGHT):
    """绘制回撤曲线"""
    dd = report.get("drawdownCurve", [])
    if not dd:
        print("report_json 中没有 drawdownCurve 数据")
        return

    setup_plot("Drawdown", height)
    plt.plot(dd, label="Drawdown", color="red")
    plt.show()
    print()


def plot_filled_orders(chart, report, height=DEFAULT_HEIGHT):
    """在权益曲线上标注买卖点"""
    equity = report.get("equityCurve", [])
    filled = chart.get("filled_orders", {})
    if not equity or not filled:
        return

    buy_x, buy_y = [], []
    sell_x, sell_y = [], []
    for bar_idx_str, orders in filled.items():
        bar_idx = int(bar_idx_str)
        if bar_idx < len(equity):
            for order in orders:
                if order.get("quantity", 0) > 0:
                    buy_x.append(bar_idx)
                    buy_y.append(equity[bar_idx])
                else:
                    sell_x.append(bar_idx)
                    sell_y.append(equity[bar_idx])

    setup_plot("Equity + Trades", height)
    plt.plot(equity, label="Equity", color="green")
    if buy_x:
        plt.scatter(buy_x, buy_y, label="Buy", color="cyan", marker="dot")
    if sell_x:
        plt.scatter(sell_x, sell_y, label="Sell", color="red", marker="dot")
    plt.show()
    print()


def main():
    parser = argparse.ArgumentParser(description="终端绘制 quant-run 图表")
    parser.add_argument("file", help="RunScript API 响应 JSON 文件")
    parser.add_argument("--plots", help="只绘制指定标题的 plot（逗号分隔）", default=None)
    parser.add_argument("--equity", action="store_true", help="绘制权益曲线 + 买入持有")
    parser.add_argument("--drawdown", action="store_true", help="绘制回撤曲线")
    parser.add_argument("--trades", action="store_true", help="绘制权益曲线并标注买卖点")
    parser.add_argument("--all", action="store_true", help="全部绘制")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=f"图表高度行数（默认 {DEFAULT_HEIGHT}）")
    args = parser.parse_args()

    report, chart = load_response(args.file)
    h = args.height

    no_flag = not (args.equity or args.drawdown or args.trades or args.all)

    if args.all or no_flag:
        filter_titles = args.plots.split(",") if args.plots else None
        plot_series(chart, filter_titles, h)

    if args.all or args.equity:
        plot_equity(report, h)

    if args.all or args.trades:
        plot_filled_orders(chart, report, h)

    if args.all or args.drawdown:
        plot_drawdown(report, h)


if __name__ == "__main__":
    main()
