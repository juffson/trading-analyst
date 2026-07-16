"""下单前的硬性风控闸门。任何一项不满足就拒绝，不下单。

这一层和"信号从哪来"完全无关——不管信号是规则策略算出来的还是以后接了 LLM 二次过滤，
每一笔要执行的操作都必须先过这里。
"""
from dataclasses import dataclass


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = "ok"


def check(side, qty, price, risk_cfg, state):
    """
    side: "buy" | "sell"
    risk_cfg: config/strategies.json 里该标的的 "risk" 字段
    state: state_store.load(symbol) 的返回值
    """
    positions = state.get("positions", {})
    daily = state.get("daily", {})

    max_shares = risk_cfg.get("max_position_shares")
    if side == "buy" and max_shares is not None:
        projected = positions.get("shares", 0) + qty
        if projected > max_shares:
            return RiskDecision(False, f"超出单标的持仓上限 {max_shares} 股（当前+本次={projected}）")

    max_orders = risk_cfg.get("max_orders_per_day")
    if max_orders is not None and daily.get("orders_count", 0) >= max_orders:
        return RiskDecision(False, f"已达当日下单次数上限 {max_orders}")

    max_daily_loss_pct = risk_cfg.get("max_daily_loss_pct")
    if max_daily_loss_pct is not None and daily.get("realized_pnl_pct", 0) <= -abs(max_daily_loss_pct):
        return RiskDecision(False, f"当日已亏损超过熔断线 {max_daily_loss_pct}%，禁止新开仓")

    return RiskDecision(True)
