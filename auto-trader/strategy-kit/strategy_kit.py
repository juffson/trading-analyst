#!/usr/bin/env python3
"""策略片段库（strategy-kit）：列出/查看/组合内置策略片段，生成可直接喂给 quant-backtest 的
QuantScript。

数据来自 strategy_kit.json（翻译自 ../../facts-hub/pkg/test/factor_lib.json 的因子定义——
id/direction/groups/description 尽量保持一致，方便对照 facts-hub 那边的原始定义，但这里的
抽象叫「策略片段/block」，不是 facts-hub 里"驱动 Fact 的因子"那个概念）。

每个片段只存「设置代码」(setup) + 「触发条件」(condition)，不是完整脚本——完整脚本由
render_paired() / compose() 在运行时拼出来，理由：
  - 同一指标的 long/short 两个片段（比如 rsi_14_long / rsi_14_short）本来就该共享同一份
    setup（同一个 rsiValue），拼成一个「开仓/平仓」的完整策略，而不是各自变成孤立脚本
  - 不同片段的变量名和 input 参数名在设计时就保证了全局唯一（rsiLength / bbLength /
    wprLength 而不是都叫 length），两个片段的 setup 混在一起拼接时不会互相覆盖

用法（CLI）:
    python3 strategy_kit.py list
    python3 strategy_kit.py show rsi_14_long
    python3 strategy_kit.py render rsi_14 --out /tmp/rsi_14.pine
    python3 strategy_kit.py compose --entry breakout --exit bearish_divergence --out /tmp/combo.pine
"""
import argparse
import json
from pathlib import Path

LIB_PATH = Path(__file__).resolve().parent / "strategy_kit.json"

_STRATEGY_HEADER = """//@version=6
strategy("{name}", overlay=false, initial_capital={capital})
"""


def _load():
    return json.loads(LIB_PATH.read_text(encoding="utf-8"))["blocks"]


def list_blocks(implemented_only=False):
    """返回 [(id, block_dict), ...]，implemented_only=True 时过滤掉 implemented=false 的。"""
    blocks = _load()
    items = sorted(blocks.items())
    if implemented_only:
        items = [(bid, b) for bid, b in items if b.get("implemented")]
    return items


def get_block(block_id):
    blocks = _load()
    if block_id not in blocks:
        raise KeyError(f"未知片段 id: {block_id!r}；用 `strategy_kit.py list` 看可用列表")
    return blocks[block_id]


def _dedupe_lines(lines):
    """保留顺序去重——两个片段共享同一行 setup（比如都算了 rsiValue）时只留一份。"""
    seen = set()
    out = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _render_inputs(inputs):
    lines = []
    for inp in inputs:
        default = inp["default"]
        lines.append(f'{inp["var"]} = input.{"float" if isinstance(default, float) else "int"}'
                      f'({default}, title="{inp["label"]}")')
    return lines


def _build_script(strategy_name, entry_inputs, entry_setup, entry_condition, entry_side,
                   exit_inputs, exit_setup, exit_condition, qty, capital):
    inputs = _dedupe_lines(_render_inputs(entry_inputs) + _render_inputs(exit_inputs))
    setup = _dedupe_lines(entry_setup + exit_setup)

    long_or_short = "strategy.long" if entry_side == "long" else "strategy.short"
    position_id = "Long" if entry_side == "long" else "Short"

    lines = [_STRATEGY_HEADER.format(name=strategy_name, capital=capital).rstrip()]
    lines.extend(inputs)
    lines.extend(setup)
    lines.append(f"if {entry_condition}")
    lines.append(f'    strategy.entry("{position_id}", {long_or_short}, qty={qty})')
    lines.append(f"if {exit_condition}")
    lines.append(f'    strategy.close("{position_id}")')
    return "\n".join(lines) + "\n"


def render_paired(base_name, qty=100, capital=100000):
    """base_name 如 'rsi_14'：自动找同名的 <base_name>_long 和 <base_name>_short 两个片段，
    拼成一个『同一指标反转』策略——多头信号开仓，空头信号平仓。要求两者都 implemented=true。
    """
    blocks = _load()
    long_id, short_id = f"{base_name}_long", f"{base_name}_short"
    if long_id not in blocks or short_id not in blocks:
        raise KeyError(f"找不到 {long_id} / {short_id} 这一对——用 `list` 看可用片段 id")
    long_b, short_b = blocks[long_id], blocks[short_id]
    if not (long_b.get("implemented") and short_b.get("implemented")):
        raise ValueError(f"{base_name} 的 long/short 片段里有一个 implemented=false，不能生成")

    return _build_script(
        base_name,
        long_b["inputs"], long_b["setup"], long_b["condition"], long_b["direction"],
        short_b["inputs"], short_b["setup"], short_b["condition"],
        qty, capital,
    )


def compose(entry_id, exit_id, qty=100, capital=100000):
    """entry_id/exit_id 是完整片段 id（比如 entry='breakout', exit='bearish_divergence'）。
    两者可以是任意两个片段——不要求方向相反或名字配对，但通常应该选逻辑上互补的一对
    （每个片段的 pair_with 字段给了推荐搭配）。
    """
    entry_b = get_block(entry_id)
    exit_b = get_block(exit_id)
    if not (entry_b.get("implemented") and exit_b.get("implemented")):
        raise ValueError(f"{entry_id} 或 {exit_id} 的 implemented=false，不能生成")

    return _build_script(
        f"{entry_id}_entry_{exit_id}_exit",
        entry_b["inputs"], entry_b["setup"], entry_b["condition"], entry_b["direction"],
        exit_b["inputs"], exit_b["setup"], exit_b["condition"],
        qty, capital,
    )


def _cmd_list(args):
    for bid, b in list_blocks(implemented_only=args.implemented_only):
        flag = "" if b.get("implemented") else "  [未实现]"
        print(f"{bid:28s} {b.get('direction','-'):6s} {b.get('description','')}{flag}")


def _cmd_show(args):
    b = get_block(args.block_id)
    print(json.dumps(b, ensure_ascii=False, indent=2))


def _cmd_render(args):
    script = render_paired(args.base_name, qty=args.qty, capital=args.capital)
    if args.out:
        Path(args.out).write_text(script, encoding="utf-8")
        print(f"已写入: {args.out}")
    else:
        print(script)


def _cmd_compose(args):
    script = compose(args.entry, args.exit, qty=args.qty, capital=args.capital)
    if args.out:
        Path(args.out).write_text(script, encoding="utf-8")
        print(f"已写入: {args.out}")
    else:
        print(script)


def main():
    parser = argparse.ArgumentParser(description="内置策略片段库管理 (strategy-kit)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出所有策略片段")
    p_list.add_argument("--implemented-only", action="store_true", dest="implemented_only")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="查看某个策略片段的完整定义")
    p_show.add_argument("block_id")
    p_show.set_defaults(func=_cmd_show)

    p_render = sub.add_parser("render", help="把同名 long/short 片段拼成一份完整 QuantScript")
    p_render.add_argument("base_name", help="如 rsi_14 / macd_12_26_9 / bollinger_bands_20 / williams_14")
    p_render.add_argument("--qty", type=int, default=100)
    p_render.add_argument("--capital", type=float, default=100000)
    p_render.add_argument("--out", help="写入文件路径（不给就打印到 stdout）")
    p_render.set_defaults(func=_cmd_render)

    p_compose = sub.add_parser("compose", help="用任意两个片段（entry 开仓 / exit 平仓）拼一份 QuantScript")
    p_compose.add_argument("--entry", required=True)
    p_compose.add_argument("--exit", required=True)
    p_compose.add_argument("--qty", type=int, default=100)
    p_compose.add_argument("--capital", type=float, default=100000)
    p_compose.add_argument("--out", help="写入文件路径（不给就打印到 stdout）")
    p_compose.set_defaults(func=_cmd_compose)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
