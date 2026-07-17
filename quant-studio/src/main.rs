mod factors;
mod live_trading;
mod longbridge;
mod paper_trading;
mod performance;
mod skill_runner;

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Path as AxumPath, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Json as JsonResponse};
use axum::routing::{get, post};
use axum::Router;
use chrono::NaiveDate;
use serde::Deserialize;
use serde_json::{json, Value};
use tower_http::services::ServeDir;

use factors::FactorLib;
use performance::PerformanceLog;

struct AppState {
    factor_lib_path: PathBuf,
    perf: PerformanceLog,
    auto_trader_config_path: PathBuf,
    auto_trader_watchlist_dir: PathBuf,
    auto_trader_state_dir: PathBuf,
    trading_analyst_root: PathBuf,
    company_deep_dive_skill_dir: PathBuf,
    trading_analyst_skill_dir: PathBuf,
    company_analysis_dir: PathBuf,
    trade_review_dir: PathBuf,
}

type SharedState = Arc<AppState>;

#[tokio::main]
async fn main() {
    let root = std::env::current_dir().expect("cwd");
    let auto_trader_root = root.join("../auto-trader");
    let trading_analyst_root = std::fs::canonicalize(root.join(".."))
        .unwrap_or_else(|_| root.join(".."));
    let state = Arc::new(AppState {
        factor_lib_path: root.join("data/strategy_kit.json"),
        perf: PerformanceLog::new(root.join("data/performance")),
        auto_trader_config_path: auto_trader_root.join("config/strategies.json"),
        auto_trader_watchlist_dir: auto_trader_root.join("watchlist"),
        auto_trader_state_dir: auto_trader_root.join("state"),
        company_deep_dive_skill_dir: trading_analyst_root.join("company-deep-dive"),
        trading_analyst_skill_dir: trading_analyst_root.join("trading-analyst"),
        company_analysis_dir: root.join("data/company-analysis"),
        trade_review_dir: root.join("data/trade-review"),
        trading_analyst_root,
    });

    let app = Router::new()
        .route("/api/factors", get(list_factors))
        .route("/api/backtest", post(run_backtest))
        .route("/api/performance/{key}", get(performance_history))
        .route("/api/paper-trading", get(paper_trading_snapshot))
        .route("/api/live-trading", get(live_trading_snapshot))
        .route(
            "/api/company-analysis",
            get(list_company_analysis).post(start_company_analysis),
        )
        .route(
            "/api/company-analysis/{slug}/status",
            get(company_analysis_status),
        )
        .route(
            "/api/company-analysis/{slug}/report",
            get(company_analysis_report),
        )
        .route(
            "/api/trade-review",
            get(list_trade_review).post(start_trade_review),
        )
        .route("/api/trade-review/{slug}/status", get(trade_review_status))
        .route("/api/trade-review/{slug}/report", get(trade_review_report))
        .fallback_service(ServeDir::new(root.join("static")))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:4870")
        .await
        .expect("bind 127.0.0.1:4870 失败——端口可能被占用");
    println!("quant-studio 监听 http://127.0.0.1:4870");
    axum::serve(listener, app).await.expect("server error");
}

fn err_response(err: anyhow::Error) -> (StatusCode, JsonResponse<Value>) {
    (
        StatusCode::BAD_REQUEST,
        JsonResponse(json!({ "error": err.to_string() })),
    )
}

async fn list_factors(State(state): State<SharedState>) -> impl IntoResponse {
    match FactorLib::load(&state.factor_lib_path) {
        Ok(lib) => {
            let mut items: Vec<Value> = lib
                .blocks
                .iter()
                .map(|(id, f)| {
                    json!({
                        "id": id,
                        "name": f.name,
                        "direction": f.direction,
                        "description": f.description,
                        "implemented": f.implemented,
                        "pair_with": f.pair_with,
                    })
                })
                .collect();
            items.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
            (StatusCode::OK, JsonResponse(json!({ "factors": items }))).into_response()
        }
        Err(e) => err_response(e).into_response(),
    }
}

#[derive(Deserialize)]
struct BacktestRequest {
    symbol: String,
    /// "paired" 用 base_name（同名 long/short 配对）；"compose" 用 entry_id/exit_id
    mode: String,
    base_name: Option<String>,
    entry_id: Option<String>,
    exit_id: Option<String>,
    #[serde(default = "default_period")]
    period: String,
    #[serde(default = "default_lookback")]
    lookback_days: i64,
    inputs_json: Option<String>,
    #[serde(default = "default_qty")]
    qty: i64,
    #[serde(default = "default_capital")]
    capital: f64,
    /// 可选：显式指定回测区间，不给就用 lookback_days 相对今天倒推
    start: Option<String>,
    end: Option<String>,
}

fn default_period() -> String {
    "day".to_string()
}
fn default_lookback() -> i64 {
    365
}
fn default_qty() -> i64 {
    100
}
fn default_capital() -> f64 {
    100_000.0
}

async fn run_backtest(
    State(state): State<SharedState>,
    JsonResponse(req): JsonResponse<BacktestRequest>,
) -> impl IntoResponse {
    let lib = match FactorLib::load(&state.factor_lib_path) {
        Ok(lib) => lib,
        Err(e) => return err_response(e).into_response(),
    };

    let (script, factor_key) = match req.mode.as_str() {
        "paired" => {
            let base = match &req.base_name {
                Some(b) => b.clone(),
                None => return err_response(anyhow::anyhow!("mode=paired 需要 base_name")).into_response(),
            };
            match lib.render_paired(&base, req.qty, req.capital) {
                Ok(script) => (script, base),
                Err(e) => return err_response(e).into_response(),
            }
        }
        "compose" => {
            let (entry, exit) = match (&req.entry_id, &req.exit_id) {
                (Some(e), Some(x)) => (e.clone(), x.clone()),
                _ => return err_response(anyhow::anyhow!("mode=compose 需要 entry_id 和 exit_id")).into_response(),
            };
            let key = format!("{entry}+{exit}");
            match lib.compose(&entry, &exit, req.qty, req.capital) {
                Ok(script) => (script, key),
                Err(e) => return err_response(e).into_response(),
            }
        }
        other => {
            return err_response(anyhow::anyhow!("未知 mode={other}，只接受 paired|compose")).into_response();
        }
    };

    if !performance::is_safe_key(&factor_key) {
        return err_response(anyhow::anyhow!("非法 factor key: {factor_key}")).into_response();
    }

    let inputs_json = req.inputs_json.unwrap_or_else(|| "[]".to_string());

    let result = if let (Some(start_s), Some(end_s)) = (&req.start, &req.end) {
        let parse = |s: &str| NaiveDate::parse_from_str(s, "%Y-%m-%d");
        match (parse(start_s), parse(end_s)) {
            (Ok(start), Ok(end)) => {
                longbridge::run_backtest_range(&req.symbol, &script, start, end, &req.period, &inputs_json).await
            }
            _ => return err_response(anyhow::anyhow!("start/end 需要 YYYY-MM-DD 格式")).into_response(),
        }
    } else {
        longbridge::run_backtest(&req.symbol, &script, req.lookback_days, &req.period, &inputs_json).await
    };

    let backtest = match result {
        Ok(r) => r,
        Err(e) => return err_response(e).into_response(),
    };

    let metrics = longbridge::extract_metrics(&backtest.report);
    let bar_times = longbridge::extract_bar_times(&backtest.events);
    let candles = longbridge::extract_candles(&backtest.events);

    if let Err(e) = state.perf.append(&factor_key, &req.symbol, &metrics) {
        eprintln!("记录 performance 失败（不影响本次回测结果返回）: {e}");
    }

    (
        StatusCode::OK,
        JsonResponse(json!({
            "factor_key": factor_key,
            "script": script,
            "report": backtest.report,
            "chart": backtest.chart,
            "bar_times": bar_times,
            "candles": candles,
            "metrics": metrics,
        })),
    )
        .into_response()
}

async fn performance_history(
    State(state): State<SharedState>,
    AxumPath(key): AxumPath<String>,
) -> impl IntoResponse {
    if !performance::is_safe_key(&key) {
        return err_response(anyhow::anyhow!("非法 factor key: {key}")).into_response();
    }
    match state.perf.history(&key) {
        Ok(history) => (StatusCode::OK, JsonResponse(json!({ "history": history }))).into_response(),
        Err(e) => err_response(e).into_response(),
    }
}

/// "模拟交易"只读视图——读 auto-trader 本地的 watchlist/plans.jsonl + state/*.json，
/// 不重新实现它的执行逻辑，纯展示。目录不存在（auto-trader 还没跑过）时返回空列表，不报错。
async fn paper_trading_snapshot(State(state): State<SharedState>) -> impl IntoResponse {
    match paper_trading::snapshot(
        &state.auto_trader_config_path,
        &state.auto_trader_watchlist_dir,
        &state.auto_trader_state_dir,
    ) {
        Ok(data) => (StatusCode::OK, JsonResponse(data)).into_response(),
        Err(e) => err_response(e).into_response(),
    }
}

/// "实盘交易"只读视图——直接查 Longbridge 账户真实持仓 + 今日委托（TradeContext），
/// 不经过本地文件，因为 auto-trader 里没有任何自动路径会产生真实成交记录可读。
async fn live_trading_snapshot() -> impl IntoResponse {
    match live_trading::snapshot().await {
        Ok(data) => (StatusCode::OK, JsonResponse(data)).into_response(),
        Err(e) => err_response(e).into_response(),
    }
}

/// 让 claude 自己读 SKILL.md 按流程做，不依赖 skill 自动发现/匹配（用户选了不把这两个 skill
/// 装进 ~/.claude/skills/，避免变成全局配置副作用）。
fn skill_prompt_header(skill_dir: &std::path::Path) -> String {
    format!(
        "请读取并严格执行 {dir}/SKILL.md 里描述的完整工作流——把该文件里提到的 SKILL_DIR 直接当成 \
         {dir}，所有 scripts/ 和 references/ 相对路径都基于这个目录解析。\n\n",
        dir = skill_dir.display()
    )
}

/// 拼成一条可以直接复制到终端跑的命令。**不加 --permission-mode bypassPermissions**——
/// 这两个 skill 需要 Bash/网络/子 agent 权限，交给用户自己在交互式终端里逐条确认，
/// quant-studio 后端不代跑、不无人值守执行任何东西。
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn build_command(prompt: &str, cwd: &std::path::Path) -> String {
    format!(
        "cd {} && claude -p {}",
        shell_quote(&cwd.display().to_string()),
        shell_quote(prompt),
    )
}

#[derive(Deserialize)]
struct CompanyAnalysisRequest {
    name: String,
}

async fn list_company_analysis(State(state): State<SharedState>) -> impl IntoResponse {
    let jobs = skill_runner::list_jobs(&state.company_analysis_dir, "deep_dive.html");
    JsonResponse(json!({ "jobs": jobs }))
}

async fn start_company_analysis(
    State(state): State<SharedState>,
    JsonResponse(req): JsonResponse<CompanyAnalysisRequest>,
) -> impl IntoResponse {
    let name = req.name.trim().to_string();
    if name.is_empty() {
        return err_response(anyhow::anyhow!("公司名/代码不能为空")).into_response();
    }
    let slug = skill_runner::new_job_slug(&name);
    let job_dir = state.company_analysis_dir.join(&slug);
    if let Err(e) = std::fs::create_dir_all(&job_dir) {
        return err_response(anyhow::anyhow!("创建任务目录失败: {e}")).into_response();
    }
    let out_dir = std::fs::canonicalize(&job_dir).unwrap_or(job_dir.clone());
    let skill_dir = std::fs::canonicalize(&state.company_deep_dive_skill_dir)
        .unwrap_or_else(|_| state.company_deep_dive_skill_dir.clone());

    let prompt = format!(
        "{header}任务：对 \"{name}\" 做买入前的深度价值分析（11 维度打分 + DCF + HTML 决策仪表盘）。\n\
         完整仪表盘存到 {out}/deep_dive.html（必须用 render_dashboard.py 生成，不要自己手写 HTML）；\n\
         另外在 {out}/summary.json 写一份精简摘要，字段：company, code, score_total, score_max, \
         recommendation_band, buy_price_range, margin_of_safety_pct, generated_at。",
        header = skill_prompt_header(&skill_dir),
        name = name,
        out = out_dir.display(),
    );
    let command = build_command(&prompt, &state.trading_analyst_root);

    if let Err(e) = skill_runner::prepare_job(&job_dir, &command) {
        return err_response(anyhow::anyhow!("准备任务失败: {e}")).into_response();
    }

    (
        StatusCode::OK,
        JsonResponse(json!({ "slug": slug, "command": command })),
    )
        .into_response()
}

async fn company_analysis_status(
    State(state): State<SharedState>,
    AxumPath(slug): AxumPath<String>,
) -> impl IntoResponse {
    if !skill_runner::is_safe_slug(&slug) {
        return err_response(anyhow::anyhow!("非法 slug: {slug}")).into_response();
    }
    JsonResponse(skill_runner::job_status(
        &state.company_analysis_dir.join(&slug),
        "deep_dive.html",
    ))
    .into_response()
}

async fn company_analysis_report(
    State(state): State<SharedState>,
    AxumPath(slug): AxumPath<String>,
) -> impl IntoResponse {
    if !skill_runner::is_safe_slug(&slug) {
        return err_response(anyhow::anyhow!("非法 slug: {slug}")).into_response();
    }
    match skill_runner::read_report_html(&state.company_analysis_dir.join(&slug), "deep_dive.html") {
        Ok(html) => Html(html).into_response(),
        Err(e) => err_response(anyhow::anyhow!("报告还没生成或读取失败: {e}")).into_response(),
    }
}

#[derive(Deserialize)]
struct TradeReviewRequest {
    symbol: String,
}

async fn list_trade_review(State(state): State<SharedState>) -> impl IntoResponse {
    let jobs = skill_runner::list_jobs(&state.trade_review_dir, "review.html");
    JsonResponse(json!({ "jobs": jobs }))
}

async fn start_trade_review(
    State(state): State<SharedState>,
    JsonResponse(req): JsonResponse<TradeReviewRequest>,
) -> impl IntoResponse {
    let symbol = req.symbol.trim().to_string();
    if symbol.is_empty() {
        return err_response(anyhow::anyhow!("symbol 不能为空")).into_response();
    }

    // 把我们已经有的模拟交易 + 实盘账户记录整理成一份 JSON，喂给 trading-analyst skill 做复盘，
    // 不依赖它自己 plan_io.py 存的历史 plan（我们本来就没有那份文件）。
    let paper = paper_trading::snapshot(
        &state.auto_trader_config_path,
        &state.auto_trader_watchlist_dir,
        &state.auto_trader_state_dir,
    )
    .unwrap_or(json!({ "symbols": [] }));
    let paper_symbol = paper
        .get("symbols")
        .and_then(Value::as_array)
        .and_then(|arr| arr.iter().find(|s| s["symbol"].as_str() == Some(symbol.as_str())))
        .cloned()
        .unwrap_or(Value::Null);
    let live = live_trading::snapshot()
        .await
        .unwrap_or(json!({ "positions": [], "today_orders": [] }));
    let live_filtered = json!({
        "positions": live.get("positions").and_then(Value::as_array)
            .map(|arr| arr.iter().filter(|p| p["symbol"].as_str() == Some(symbol.as_str())).cloned().collect::<Vec<_>>())
            .unwrap_or_default(),
        "today_orders": live.get("today_orders").and_then(Value::as_array)
            .map(|arr| arr.iter().filter(|o| o["symbol"].as_str() == Some(symbol.as_str())).cloned().collect::<Vec<_>>())
            .unwrap_or_default(),
    });

    let slug = skill_runner::new_job_slug(&symbol);
    let job_dir = state.trade_review_dir.join(&slug);
    if let Err(e) = std::fs::create_dir_all(&job_dir) {
        return err_response(anyhow::anyhow!("创建任务目录失败: {e}")).into_response();
    }
    let records = json!({ "symbol": symbol, "paper": paper_symbol, "live": live_filtered });
    if let Err(e) = std::fs::write(
        job_dir.join("records.json"),
        serde_json::to_string_pretty(&records).unwrap_or_default(),
    ) {
        return err_response(anyhow::anyhow!("写 records.json 失败: {e}")).into_response();
    }

    let out_dir = std::fs::canonicalize(&job_dir).unwrap_or(job_dir.clone());
    let skill_dir = std::fs::canonicalize(&state.trading_analyst_skill_dir)
        .unwrap_or_else(|_| state.trading_analyst_skill_dir.clone());

    let prompt = format!(
        "{header}任务：对 {symbol} 做「模式5：跟进复盘」。注意——这次不是走 skill 自己 plan_io.py \
         存的历史 plan，而是本机 quant-studio 工具已经收集好的模拟交易 + 实盘账户实际记录，见 \
         {out}/records.json（字段说明：paper.plans 是 auto-trader 产生的交易计划/信号，\
         paper.trade_log 是模拟成交，live.positions/live.today_orders 是 Longbridge 账户当前真实持仓和\
         今日委托）。请基于这份记录 + 最新行情做复盘分析，说清楚哪些操作合理、哪些有问题、下一步怎么调整，\
         按「无基线复盘」的方式处理（没有旧 plan 可比对，直接告知用户这一点）。\n\
         复盘 HTML 存到 {out}/review.html；另外在 {out}/summary.json 写精简摘要，字段：\n\
         symbol, verdict(on_track|watch|off_track), summary, next_actions, generated_at。",
        header = skill_prompt_header(&skill_dir),
        symbol = symbol,
        out = out_dir.display(),
    );
    let command = build_command(&prompt, &state.trading_analyst_root);

    if let Err(e) = skill_runner::prepare_job(&job_dir, &command) {
        return err_response(anyhow::anyhow!("准备任务失败: {e}")).into_response();
    }

    (
        StatusCode::OK,
        JsonResponse(json!({ "slug": slug, "command": command })),
    )
        .into_response()
}

async fn trade_review_status(
    State(state): State<SharedState>,
    AxumPath(slug): AxumPath<String>,
) -> impl IntoResponse {
    if !skill_runner::is_safe_slug(&slug) {
        return err_response(anyhow::anyhow!("非法 slug: {slug}")).into_response();
    }
    JsonResponse(skill_runner::job_status(
        &state.trade_review_dir.join(&slug),
        "review.html",
    ))
    .into_response()
}

async fn trade_review_report(
    State(state): State<SharedState>,
    AxumPath(slug): AxumPath<String>,
) -> impl IntoResponse {
    if !skill_runner::is_safe_slug(&slug) {
        return err_response(anyhow::anyhow!("非法 slug: {slug}")).into_response();
    }
    match skill_runner::read_report_html(&state.trade_review_dir.join(&slug), "review.html") {
        Ok(html) => Html(html).into_response(),
        Err(e) => err_response(anyhow::anyhow!("报告还没生成或读取失败: {e}")).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::factors::FactorLib;

    #[test]
    fn render_smoke() {
        let lib = FactorLib::load("data/strategy_kit.json").unwrap();
        let script = lib.render_paired("rsi_14", 100, 100000.0).unwrap();
        println!("{script}");
        assert!(script.contains("plot(rsiValue"));
        assert!(script.contains("plot(close"));

        let combo = lib.compose("breakout", "bearish_divergence", 100, 100000.0).unwrap();
        println!("{combo}");
        assert!(combo.contains("plot(macdHist"));
        assert!(combo.contains("plot(bbUpper"));
    }
}
