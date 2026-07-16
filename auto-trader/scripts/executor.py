"""下单执行层——复用 trading-analyst/scripts/lb_client.py 的下单命令，不重新实现下单逻辑。

刻意把「预览」和「真实下单」拆成两个独立函数：run_cycle.py 目前只调用 place_order()（永远是
--dry-run 预览，不会真实下单）。confirm_order() 需要调用方显式调用，且只应该在你自己确认过
config 里 mode == "live" 并且清楚这是真钱之后才用——没有任何自动路径会从预览升级成真实下单。
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LB_CLIENT_PY = REPO_ROOT / "trading-analyst" / "scripts" / "lb_client.py"


class ExecutionError(RuntimeError):
    pass


def _run_lb(args):
    cmd = [sys.executable, str(LB_CLIENT_PY)] + args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ExecutionError(f"lb_client.py 调用失败: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def place_order(symbol, side, qty, price, order_type="LO", remark="auto-trader"):
    """预览下单（--dry-run）。这个函数永远只做预览，不会真实下单——
    是否要往下走到 confirm_order() 完全由调用方（run_cycle.py 里的 execution_mode 判断）决定，
    这里不接受任何"live"开关。
    """
    subcmd = "order-buy" if side == "buy" else "order-sell"
    args = [
        subcmd, symbol,
        "--qty", str(qty),
        "--price", str(price),
        "--order-type", order_type,
        "--remark", remark,
        "--dry-run",
    ]
    return _run_lb(args)


def confirm_order(symbol, side, qty, price, order_type="LO", remark="auto-trader"):
    """真实下单（--confirm）。只应该在 config 里 mode == "live" 且经过你本人明确确认后调用，
    run_cycle.py 当前不会自动调用这个函数。
    """
    subcmd = "order-buy" if side == "buy" else "order-sell"
    args = [
        subcmd, symbol,
        "--qty", str(qty),
        "--price", str(price),
        "--order-type", order_type,
        "--remark", remark,
        "--confirm",
    ]
    return _run_lb(args)
