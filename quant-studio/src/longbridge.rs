//! 调 Longbridge `/v2/quant/run_script` 跑回测。
//!
//! 用官方 `longport` Rust crate 的 `HttpClient`（`HttpClientConfig::from_apikey_env()`），
//! 和 auto-trader 的 Python 版 signal_from_backtest.py 走的是同一套鉴权/请求体字段
//! （counter_id/start_time/end_time/script/inputs_json/line_type/exclude_chart），
//! 移植过来时保持字段名一致。
//!
//! `report_json`/`chart_json`/`events_json` 三个字段拿到手是字符串（内层还是一份 JSON），
//! 这里统一再 parse 成 `serde_json::Value`，不建强类型 struct——这套接口的字段命名有过
//! 实测确认才靠得住（比如 chart_json 里是驼峰 filledOrders，不是文档写的 filled_orders），
//! 用 Value 动态取字段比强行套一个可能对不上的结构体更稳。
use anyhow::{Context, Result};
use chrono::{Duration, NaiveDate, Utc};
use longport::counter::symbol_to_counter_id;
use longport::httpclient::{HttpClient, HttpClientConfig, Json, Method};
use serde_json::{Value, json};

pub struct BacktestResult {
    pub report: Value,
    pub chart: Value,
    pub events: Value,
}

fn line_type_for_period(period: &str) -> Result<i32> {
    Ok(match period {
        "1m" => 1,
        "5m" => 5,
        "15m" => 15,
        "30m" => 30,
        "1h" => 60,
        "day" => 1000,
        "week" => 2000,
        "month" => 3000,
        "year" => 4000,
        other => anyhow::bail!("不支持的 period: {other}"),
    })
}

/// language: 1 = pine（目前唯一支持的——navi 没有策略语义，见 auto-trader 那边的调研记录）
pub async fn run_backtest(
    symbol: &str,
    script: &str,
    lookback_days: i64,
    period: &str,
    inputs_json: &str,
) -> Result<BacktestResult> {
    let end = Utc::now().date_naive();
    let start = end - Duration::days(lookback_days);
    run_backtest_range(symbol, script, start, end, period, inputs_json).await
}

pub async fn run_backtest_range(
    symbol: &str,
    script: &str,
    start: NaiveDate,
    end: NaiveDate,
    period: &str,
    inputs_json: &str,
) -> Result<BacktestResult> {
    let start_ts = start
        .and_hms_opt(0, 0, 0)
        .expect("valid time")
        .and_utc()
        .timestamp();
    let end_ts = end
        .and_hms_opt(23, 59, 59)
        .expect("valid time")
        .and_utc()
        .timestamp();

    let config = HttpClientConfig::from_apikey_env()
        .context("HttpClientConfig::from_apikey_env 失败——检查 LONGPORT_APP_KEY/APP_SECRET/ACCESS_TOKEN")?;
    let client = HttpClient::new(config);

    let body = json!({
        "counter_id": symbol_to_counter_id(symbol),
        "start_time": start_ts,
        "end_time": end_ts,
        "script": script,
        "inputs_json": inputs_json,
        "line_type": line_type_for_period(period)?,
        "exclude_chart": false,
        "language": 1,
    });

    #[derive(serde::Deserialize)]
    struct RawResponse {
        report_json: Option<String>,
        chart_json: Option<String>,
        events_json: Option<String>,
    }

    let resp = client
        .request(Method::POST, "/v2/quant/run_script")
        .body(Json(body))
        .response::<Json<RawResponse>>()
        .send()
        .await
        .context("POST /v2/quant/run_script 失败")?
        .0;

    let parse = |s: Option<String>| -> Result<Value> {
        match s {
            Some(text) if !text.is_empty() => {
                serde_json::from_str(&text).context("解析回测响应内层 JSON 失败")
            }
            _ => Ok(Value::Null),
        }
    };

    Ok(BacktestResult {
        report: parse(resp.report_json)?,
        chart: parse(resp.chart_json)?,
        events: parse(resp.events_json)?,
    })
}

/// 从 report.performanceAll 里挑出用来记录"持续跟进效果"的关键指标。
pub fn extract_metrics(report: &Value) -> Value {
    let perf = report.get("performanceAll").cloned().unwrap_or(Value::Null);
    json!({
        "sharpe_ratio": perf.get("sharpeRatio"),
        "max_drawdown_pct": perf.get("maxDrawdownPercent"),
        "win_rate_pct": perf.get("percentProfitable"),
        "total_closed_trades": perf.get("totalClosedTrades"),
        "total_open_trades": perf.get("totalOpenTrades"),
        "net_profit_pct": perf.get("netProfitPercent"),
    })
}

/// chart_json.seriesGraphs 里的每条 series 只是一个按 bar_index 排列的数值数组，本身不带
/// 日期——events_json 里的 barStart 事件才有时间和整根 K 线。实测字段名是驼峰嵌套
/// `{"barStart": {"barIndex": N, "candlestick": {"time": ms, ...}}}`，不是文档/直觉会猜的
/// `BarStart`/`bar_index`/顶层 `timestamp`（这个接口目前每个字段名都得实测一遍才能信）。
/// 时间戳统一转成 Unix 秒（lightweight-charts 要秒不是毫秒）。
pub fn extract_bar_times(events: &Value) -> Vec<i64> {
    let Some(arr) = events.as_array() else {
        return Vec::new();
    };
    let mut times: Vec<(i64, i64)> = Vec::new();
    for ev in arr {
        if let Some(bar_start) = ev.get("barStart") {
            let bar_index = bar_start.get("barIndex").and_then(Value::as_i64);
            let ts_ms = bar_start
                .get("candlestick")
                .and_then(|c| c.get("time"))
                .and_then(Value::as_i64);
            if let (Some(idx), Some(ts)) = (bar_index, ts_ms) {
                times.push((idx, ts / 1000));
            }
        }
    }
    times.sort_by_key(|(idx, _)| *idx);
    times.into_iter().map(|(_, ts)| ts).collect()
}

/// 每个 barStart 事件本身就带了完整 OHLCV（`candlestick` 字段）——不用另外接
/// QuoteContext/WebSocket 拉K线，这次回测顺带就有真实价格数据，直接给前端画蜡烛图。
pub fn extract_candles(events: &Value) -> Vec<Value> {
    let Some(arr) = events.as_array() else {
        return Vec::new();
    };
    let mut candles: Vec<(i64, Value)> = Vec::new();
    for ev in arr {
        let Some(bar_start) = ev.get("barStart") else { continue };
        let Some(idx) = bar_start.get("barIndex").and_then(Value::as_i64) else { continue };
        let Some(c) = bar_start.get("candlestick") else { continue };
        let (Some(time_ms), Some(open), Some(high), Some(low), Some(close)) = (
            c.get("time").and_then(Value::as_i64),
            c.get("open").and_then(Value::as_f64),
            c.get("high").and_then(Value::as_f64),
            c.get("low").and_then(Value::as_f64),
            c.get("close").and_then(Value::as_f64),
        ) else {
            continue;
        };
        candles.push((
            idx,
            json!({ "time": time_ms / 1000, "open": open, "high": high, "low": low, "close": close }),
        ));
    }
    candles.sort_by_key(|(idx, _)| *idx);
    candles.into_iter().map(|(_, c)| c).collect()
}
