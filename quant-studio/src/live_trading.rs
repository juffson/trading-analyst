//! "实盘交易" 只读视图——直接读 Longbridge 账户里真实的持仓和今日委托，不走本地文件。
//!
//! auto-trader 里没有任何自动路径会调用真实下单（`executor.confirm_order()` 只能手动调），
//! 所以本地没有"实盘成交记录"这种数据可读；这里换个思路，直接问 Longbridge 账户当前
//! 真实持仓 + 今天的委托单，这才是"实盘交易"最准确的定义——不管这笔单子是不是手动下的。
//! 只读，这个模块不提供任何下单接口。
use anyhow::{Context, Result};
use longport::trade::TradeContext;
use longport::Config;
use serde_json::{json, Value};
use std::sync::Arc;

pub async fn snapshot() -> Result<Value> {
    let config = Arc::new(
        Config::from_apikey_env()
            .context("Config::from_apikey_env 失败——检查 LONGPORT_APP_KEY/APP_SECRET/ACCESS_TOKEN")?,
    );
    let (ctx, _push_rx) = TradeContext::new(config);

    let positions_resp = ctx
        .stock_positions(None)
        .await
        .context("拉取真实持仓失败（/v1/asset/stock）")?;
    let orders = ctx
        .today_orders(None)
        .await
        .context("拉取今日委托失败（/v1/trade/order/today）")?;

    let positions: Vec<Value> = positions_resp
        .channels
        .iter()
        .flat_map(|ch| ch.positions.iter())
        .map(|p| {
            json!({
                "symbol": p.symbol,
                "symbol_name": p.symbol_name,
                "quantity": p.quantity.to_string(),
                "available_quantity": p.available_quantity.to_string(),
                "cost_price": p.cost_price.to_string(),
                "currency": p.currency,
                "market": format!("{:?}", p.market),
            })
        })
        .collect();

    let orders: Vec<Value> = orders
        .iter()
        .map(|o| {
            json!({
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": format!("{:?}", o.side),
                "status": format!("{:?}", o.status),
                "order_type": format!("{:?}", o.order_type),
                "quantity": o.quantity.to_string(),
                "executed_quantity": o.executed_quantity.to_string(),
                "price": o.price.map(|d| d.to_string()),
                "executed_price": o.executed_price.map(|d| d.to_string()),
                "submitted_at": o.submitted_at.to_string(),
                "msg": o.msg,
            })
        })
        .collect();

    Ok(json!({ "positions": positions, "today_orders": orders }))
}
