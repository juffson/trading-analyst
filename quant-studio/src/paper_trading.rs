//! "模拟交易" 只读视图——直接读 `../auto-trader` 本地产出的文件，不重新实现它的逻辑。
//!
//! auto-trader 是本机另一个独立工具，这些文件都在同一台机器的文件系统上，quant-studio 直接读
//! 文件就行，不用起 HTTP 调用。只读，这个模块不会写这些文件——写入逻辑始终留在 auto-trader
//! 那边。字段语义照抄 auto-trader 对应脚本，是它们的读取镜像，不是重新设计：
//! - `config/strategies.json` → 哪些标的在跟踪 + `execution_mode`（`run_cycle.py`）
//! - `watchlist/<symbol>/plans.jsonl` → 交易计划/信号，pending/rejected/executed（`plan_store.py`）
//! - `watchlist/<symbol>/stage.json` → 研究→模拟→实盘的阶段流转（`pipeline/advance_stage.py`）
//! - `watchlist/<symbol>/backtest_report.json` → calibrating 阶段定的回测基线（`pipeline/README.md`）
//! - `watchlist/<symbol>/trade_log.jsonl` → auto_paper 模式下的模拟成交记录（`run_cycle.py`）
//! - `state/<symbol>.json` → 持仓快照（`state_store.py`）
use anyhow::Result;
use serde_json::{json, Value};
use std::fs;
use std::path::Path;

/// 照抄 `plan_store.py::load_latest_plans`：按 plan id 去重，只留每个 plan 最新一条记录。
fn load_latest_plans(plans_path: &Path) -> Vec<Value> {
    let Ok(text) = fs::read_to_string(plans_path) else {
        return Vec::new();
    };
    let mut latest = std::collections::HashMap::<String, Value>::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(entry) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let Some(plan) = entry.get("plan") else { continue };
        let Some(id) = plan.get("id").and_then(Value::as_str) else {
            continue;
        };
        latest.insert(id.to_string(), plan.clone());
    }
    latest.into_values().collect()
}

fn load_jsonl_tail(path: &Path, limit: usize) -> Vec<Value> {
    let Ok(text) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let mut lines: Vec<Value> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect();
    if lines.len() > limit {
        lines = lines.split_off(lines.len() - limit);
    }
    lines.reverse();
    lines
}

fn load_json(path: &Path) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

pub fn snapshot(
    config_path: &Path,
    watchlist_dir: &Path,
    state_dir: &Path,
) -> Result<Value> {
    let config = load_json(config_path);
    let configured: Vec<(String, Value)> = config
        .get("symbols")
        .and_then(Value::as_object)
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();

    // 除了 config 里配置的标的，也把 watchlist/ 下有数据但 config 里已经删掉的标的捞出来，
    // 免得历史记录因为改配置就从页面消失。
    let mut symbol_names: Vec<String> = configured.iter().map(|(s, _)| s.clone()).collect();
    if let Ok(entries) = fs::read_dir(watchlist_dir) {
        for entry in entries.flatten() {
            if entry.path().is_dir() {
                let name = entry.file_name().to_string_lossy().to_string();
                if !symbol_names.contains(&name) {
                    symbol_names.push(name);
                }
            }
        }
    }

    let mut symbols: Vec<Value> = Vec::new();
    for symbol in symbol_names {
        let symbol_cfg = configured
            .iter()
            .find(|(s, _)| s == &symbol)
            .map(|(_, c)| c.clone())
            .unwrap_or(Value::Null);
        let symbol_dir = watchlist_dir.join(&symbol);

        let mut plans = load_latest_plans(&symbol_dir.join("plans.jsonl"));
        let trade_log = load_jsonl_tail(&symbol_dir.join("trade_log.jsonl"), 20);
        let stage = load_json(&symbol_dir.join("stage.json"));
        let backtest = load_json(&symbol_dir.join("backtest_report.json"));
        let state = load_json(&state_dir.join(format!("{symbol}.json")));

        if plans.is_empty() && trade_log.is_empty() && stage.is_null() && symbol_cfg.is_null() {
            continue; // 这个"标的"什么数据都没有，跳过，不占页面位置
        }

        plans.sort_by(|a, b| {
            let ka = a.get("updated_at").and_then(Value::as_str).unwrap_or("");
            let kb = b.get("updated_at").and_then(Value::as_str).unwrap_or("");
            kb.cmp(ka)
        });
        let pending_count = plans
            .iter()
            .filter(|p| p.get("status").and_then(Value::as_str) == Some("pending"))
            .count();
        let last_signal_at = plans.first().and_then(|p| p.get("created_at").cloned());

        symbols.push(json!({
            "symbol": symbol,
            "execution_mode": symbol_cfg.get("execution_mode").cloned().unwrap_or(json!("signal_only")),
            "strategy_file": symbol_cfg.get("strategy_file"),
            "stage": stage.get("stage").and_then(Value::as_str).unwrap_or("researching"),
            "ready_for_live_promotion": stage.get("ready_for_live_promotion").and_then(Value::as_bool).unwrap_or(false),
            "stage_history": stage.get("history").cloned().unwrap_or(json!([])),
            "backtest": backtest,
            "plans": plans,
            "pending_count": pending_count,
            "last_signal_at": last_signal_at,
            "trade_log": trade_log,
            "position": state.get("positions").cloned().unwrap_or(Value::Null),
            "daily": state.get("daily").cloned().unwrap_or(Value::Null),
            "state_updated_at": state.get("updated_at").cloned().unwrap_or(Value::Null),
        }));
    }

    symbols.sort_by(|a, b| a["symbol"].as_str().cmp(&b["symbol"].as_str()));

    let mut status_counts = json!({"pending": 0, "rejected": 0, "executed": 0});
    for s in &symbols {
        if let Some(plans) = s.get("plans").and_then(Value::as_array) {
            for p in plans {
                if let Some(status) = p.get("status").and_then(Value::as_str) {
                    if let Some(n) = status_counts.get(status).and_then(Value::as_i64) {
                        status_counts[status] = json!(n + 1);
                    }
                }
            }
        }
    }

    Ok(json!({ "symbols": symbols, "status_counts": status_counts }))
}
