mod cli_settings;
mod credentials;
mod factors;
mod github_research;
mod live_trading;
mod longbridge;
mod paper_trading;
mod performance;
mod skill_runner;
mod terminal;

use std::path::PathBuf;
use std::sync::Arc;

use axum::Router;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{Html, IntoResponse, Json as JsonResponse};
use axum::routing::{get, post};
use chrono::NaiveDate;
use serde::Deserialize;
use serde_json::{Value, json};
use tower_http::services::ServeDir;

use cli_settings::{ApiProfileUpdate, CliSettings, ProviderUpdate};
use credentials::{Credentials, CredentialsUpdate};
use factors::FactorLib;
use github_research::GithubResearch;
use performance::PerformanceLog;
use terminal::{Runner, SessionManager};

struct AppState {
    factor_lib_path: PathBuf,
    credentials_path: PathBuf,
    cli_settings_path: PathBuf,
    perf: PerformanceLog,
    auto_trader_config_path: PathBuf,
    auto_trader_watchlist_dir: PathBuf,
    auto_trader_state_dir: PathBuf,
    trading_analyst_root: PathBuf,
    company_deep_dive_skill_dir: PathBuf,
    trading_analyst_skill_dir: PathBuf,
    company_analysis_dir: PathBuf,
    trade_review_dir: PathBuf,
    sessions: SessionManager,
    github_research: GithubResearch,
}

type SharedState = Arc<AppState>;

#[tokio::main]
async fn main() {
    let root = std::env::current_dir().expect("cwd");
    // auto-trader 已收进 quant-studio/，是同一个产品的自动化运行器
    let auto_trader_root = root.join("auto-trader");
    let trading_analyst_root =
        std::fs::canonicalize(root.join("..")).unwrap_or_else(|_| root.join(".."));
    let sessions = SessionManager::new();
    sessions.reconcile(&root.join("data/company-analysis"));
    sessions.reconcile(&root.join("data/trade-review"));
    let state = Arc::new(AppState {
        // 因子库只有一份：auto-trader/strategy-kit/strategy_kit.json（Python CLI 和本服务共用）
        factor_lib_path: auto_trader_root.join("strategy-kit/strategy_kit.json"),
        credentials_path: root.join(".local/longbridge.json"),
        cli_settings_path: root.join(".local/cli-settings.json"),
        perf: PerformanceLog::new(root.join("data/performance")),
        auto_trader_config_path: auto_trader_root.join("config/strategies.json"),
        auto_trader_watchlist_dir: auto_trader_root.join("watchlist"),
        auto_trader_state_dir: auto_trader_root.join("state"),
        // 三个 Claude Skill 在 repo 根的 skills/ 下——这里是 repo 根，不是 skills/ 目录本身
        company_deep_dive_skill_dir: trading_analyst_root.join("skills/company-deep-dive"),
        trading_analyst_skill_dir: trading_analyst_root.join("skills/trading-analyst"),
        company_analysis_dir: root.join("data/company-analysis"),
        trade_review_dir: root.join("data/trade-review"),
        sessions,
        github_research: GithubResearch::new().expect("初始化 GitHub 研究目录失败"),
        trading_analyst_root,
    });

    let app = Router::new()
        .route("/api/factors", get(list_factors))
        .route("/api/backtest", post(run_backtest))
        .route(
            "/api/settings/longbridge",
            get(longbridge_settings).post(update_longbridge_settings),
        )
        .route("/api/settings/cli", get(cli_settings_status))
        .route("/api/settings/cli/profiles", post(save_api_profile))
        .route("/api/settings/cli/profiles/test", post(test_api_profile))
        .route(
            "/api/settings/cli/profiles/{id}",
            axum::routing::delete(delete_api_profile),
        )
        .route("/api/settings/cli/{runner}", post(update_cli_settings))
        .route("/api/performance/{key}", get(performance_history))
        .route("/api/paper-trading", get(paper_trading_snapshot))
        .route("/api/live-trading", get(live_trading_snapshot))
        .route("/api/cli/available", get(cli_available))
        .route("/api/github-research", get(github_research_index))
        .route("/api/github-research/refresh", post(refresh_github_research))
        .route("/api/github-research/{symbol}/{filename}", get(github_research_report))
        .route("/api/cli/{kind}/{slug}/terminal", get(cli_terminal))
        .route("/api/cli/{kind}/{slug}/log", get(cli_log))
        .route("/api/cli/{kind}/{slug}/stop", post(cli_stop))
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

async fn github_research_index(State(state): State<SharedState>) -> impl IntoResponse {
    match state.github_research.index(false).await {
        Ok(value) => (StatusCode::OK, JsonResponse(value)).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn refresh_github_research(State(state): State<SharedState>, headers: HeaderMap) -> impl IntoResponse {
    if !local_origin(&headers) {
        return (StatusCode::FORBIDDEN, JsonResponse(json!({"error": "只允许本地页面刷新研究目录"}))).into_response();
    }
    match state.github_research.index(true).await {
        Ok(value) => (StatusCode::OK, JsonResponse(value)).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn github_research_report(
    State(state): State<SharedState>, AxumPath((symbol, filename)): AxumPath<(String, String)>,
) -> impl IntoResponse {
    match state.github_research.report(&symbol, &filename).await {
        Ok(value) => (StatusCode::OK, JsonResponse(value)).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn longbridge_settings(State(state): State<SharedState>) -> impl IntoResponse {
    match Credentials::status(&state.credentials_path) {
        Ok(status) => (StatusCode::OK, JsonResponse(status)).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn update_longbridge_settings(
    State(state): State<SharedState>,
    headers: HeaderMap,
    JsonResponse(update): JsonResponse<CredentialsUpdate>,
) -> impl IntoResponse {
    if let Some(origin) = headers.get(header::ORIGIN) {
        if origin.to_str().ok() != Some("http://127.0.0.1:4870") {
            return (
                StatusCode::FORBIDDEN,
                JsonResponse(json!({ "error": "只允许本地应用修改凭据" })),
            )
                .into_response();
        }
    }
    match Credentials::update(&state.credentials_path, update) {
        Ok(status) => (StatusCode::OK, JsonResponse(status)).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn cli_settings_status(State(state): State<SharedState>) -> impl IntoResponse {
    match CliSettings::status(&state.cli_settings_path) {
        Ok(status) => (StatusCode::OK, JsonResponse(status)).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn update_cli_settings(
    State(state): State<SharedState>,
    AxumPath(runner): AxumPath<String>,
    headers: HeaderMap,
    JsonResponse(update): JsonResponse<ProviderUpdate>,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return (
            StatusCode::FORBIDDEN,
            JsonResponse(json!({ "error": "只允许本地页面修改 AI CLI 配置" })),
        )
            .into_response();
    }
    let runner = match runner.as_str() {
        "codex" => Runner::Codex,
        "claude" => Runner::Claude,
        _ => return err_response(anyhow::anyhow!("未知 CLI 类型")).into_response(),
    };
    match CliSettings::update(&state.cli_settings_path, runner, update) {
        Ok(status) => (StatusCode::OK, JsonResponse(status)).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn save_api_profile(
    State(state): State<SharedState>,
    headers: HeaderMap,
    JsonResponse(update): JsonResponse<ApiProfileUpdate>,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return err_response(anyhow::anyhow!("只允许本地页面修改服务商配置")).into_response();
    }
    match CliSettings::upsert_profile(&state.cli_settings_path, update) {
        Ok(status) => JsonResponse(status).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn delete_api_profile(
    State(state): State<SharedState>,
    AxumPath(id): AxumPath<String>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return err_response(anyhow::anyhow!("只允许本地页面修改服务商配置")).into_response();
    }
    match CliSettings::delete_profile(&state.cli_settings_path, &id) {
        Ok(status) => JsonResponse(status).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn test_api_profile(
    State(state): State<SharedState>,
    headers: HeaderMap,
    JsonResponse(update): JsonResponse<ApiProfileUpdate>,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return err_response(anyhow::anyhow!("只允许本地页面测试服务商配置")).into_response();
    }
    let settings = match CliSettings::load(&state.cli_settings_path) {
        Ok(settings) => settings,
        Err(error) => return err_response(error).into_response(),
    };
    let profile = match settings.resolve_profile(update) {
        Ok(profile) => profile,
        Err(error) => return err_response(error).into_response(),
    };
    match cli_settings::test_profile(&profile).await {
        Ok(status) => JsonResponse(status).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn list_factors(State(state): State<SharedState>) -> impl IntoResponse {
    match FactorLib::load(&state.factor_lib_path) {
        Ok(lib) => {
            let mut items: Vec<Value> = lib
                .blocks
                .iter()
                .map(|(id, f)| {
                    // 配对规则（_long/_short 后缀 vs pair_with）只在 factors.rs 里实现一份，
                    // 这里直接把算好的规范 key 发给前端，省得前端再猜一遍。
                    // 配不成对、或对家没实现的，就是 null——前端据此禁用 paired 模式。
                    let paired_base = lib.resolve_pair(id).ok().and_then(|(l, s)| {
                        let both_impl = lib.get(&l).map(|x| x.implemented).unwrap_or(false)
                            && lib.get(&s).map(|x| x.implemented).unwrap_or(false);
                        both_impl.then(|| l.strip_suffix("_long").unwrap_or(&l).to_string())
                    });
                    json!({
                        "id": id,
                        "name": f.name,
                        "direction": f.direction,
                        "description": f.description,
                        "implemented": f.implemented,
                        "pair_with": f.pair_with,
                        "paired_base": paired_base,
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
                None => {
                    return err_response(anyhow::anyhow!("mode=paired 需要 base_name"))
                        .into_response();
                }
            };
            let key = match lib.paired_key(&base) {
                Ok(k) => k,
                Err(e) => return err_response(e).into_response(),
            };
            match lib.render_paired(&base, req.qty, req.capital) {
                Ok(script) => (script, key),
                Err(e) => return err_response(e).into_response(),
            }
        }
        "compose" => {
            let (entry, exit) = match (&req.entry_id, &req.exit_id) {
                (Some(e), Some(x)) => (e.clone(), x.clone()),
                _ => {
                    return err_response(anyhow::anyhow!("mode=compose 需要 entry_id 和 exit_id"))
                        .into_response();
                }
            };
            let key = format!("{entry}+{exit}");
            match lib.compose(&entry, &exit, req.qty, req.capital) {
                Ok(script) => (script, key),
                Err(e) => return err_response(e).into_response(),
            }
        }
        other => {
            return err_response(anyhow::anyhow!("未知 mode={other}，只接受 paired|compose"))
                .into_response();
        }
    };

    if !performance::is_safe_key(&factor_key) {
        return err_response(anyhow::anyhow!("非法 factor key: {factor_key}")).into_response();
    }

    let inputs_json = req.inputs_json.unwrap_or_else(|| "[]".to_string());

    let credentials = match Credentials::load(&state.credentials_path) {
        Ok(credentials) => credentials,
        Err(error) => return err_response(error).into_response(),
    };

    let result = if let (Some(start_s), Some(end_s)) = (&req.start, &req.end) {
        let parse = |s: &str| NaiveDate::parse_from_str(s, "%Y-%m-%d");
        match (parse(start_s), parse(end_s)) {
            (Ok(start), Ok(end)) => {
                longbridge::run_backtest_range(
                    &credentials,
                    &req.symbol,
                    &script,
                    start,
                    end,
                    &req.period,
                    &inputs_json,
                )
                .await
            }
            _ => {
                return err_response(anyhow::anyhow!("start/end 需要 YYYY-MM-DD 格式"))
                    .into_response();
            }
        }
    } else {
        longbridge::run_backtest(
            &credentials,
            &req.symbol,
            &script,
            req.lookback_days,
            &req.period,
            &inputs_json,
        )
        .await
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
        Ok(history) => {
            (StatusCode::OK, JsonResponse(json!({ "history": history }))).into_response()
        }
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
async fn live_trading_snapshot(State(state): State<SharedState>) -> impl IntoResponse {
    let credentials = match Credentials::load(&state.credentials_path) {
        Ok(credentials) => credentials,
        Err(error) => return err_response(error).into_response(),
    };
    match live_trading::snapshot(&credentials).await {
        Ok(data) => (StatusCode::OK, JsonResponse(data)).into_response(),
        Err(e) => err_response(e).into_response(),
    }
}

async fn cli_available() -> impl IntoResponse {
    JsonResponse(SessionManager::available())
}

fn local_origin(headers: &HeaderMap) -> bool {
    headers
        .get(header::ORIGIN)
        .map(|origin| origin.to_str().ok() == Some("http://127.0.0.1:4870"))
        .unwrap_or(true)
}

fn cli_job_dir(state: &AppState, kind: &str, slug: &str) -> anyhow::Result<PathBuf> {
    if !skill_runner::is_safe_slug(slug) {
        anyhow::bail!("非法任务 ID");
    }
    let root = match kind {
        "company" => &state.company_analysis_dir,
        "trade-review" => &state.trade_review_dir,
        _ => anyhow::bail!("未知任务类型"),
    };
    let dir = root.join(slug);
    if !dir.is_dir() {
        anyhow::bail!("任务不存在");
    }
    Ok(dir)
}

async fn cli_terminal(
    State(state): State<SharedState>,
    AxumPath((kind, slug)): AxumPath<(String, String)>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return (StatusCode::FORBIDDEN, "只允许本地页面连接终端").into_response();
    }
    let dir = match cli_job_dir(&state, &kind, &slug) {
        Ok(dir) => dir,
        Err(error) => return err_response(error).into_response(),
    };
    let Some(session) = state.sessions.get(&dir) else {
        return (StatusCode::NOT_FOUND, "终端会话已结束").into_response();
    };
    ws.on_upgrade(move |socket| serve_cli_terminal(socket, session))
        .into_response()
}

async fn serve_cli_terminal(mut socket: WebSocket, session: Arc<terminal::Session>) {
    let mut output = session.subscribe();
    let replay = session.replay();
    if !replay.is_empty() && socket.send(Message::Binary(replay.into())).await.is_err() {
        return;
    }
    loop {
        tokio::select! {
            received = socket.recv() => match received {
                Some(Ok(Message::Text(text))) => {
                    let Ok(event) = serde_json::from_str::<Value>(text.as_str()) else { continue };
                    match event["type"].as_str() {
                        Some("input") => {
                            if let Some(data) = event["data"].as_str() {
                                let _ = session.input(data.as_bytes());
                            }
                        }
                        Some("resize") => {
                            if let (Some(cols), Some(rows)) = (event["cols"].as_u64(), event["rows"].as_u64()) {
                                let _ = session.resize(cols.min(u16::MAX as u64) as u16, rows.min(u16::MAX as u64) as u16);
                            }
                        }
                        _ => {}
                    }
                }
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                _ => {}
            },
            chunk = output.recv() => match chunk {
                Ok(bytes) => {
                    if socket.send(Message::Binary(bytes.into())).await.is_err() { break; }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
                Err(_) => break,
            },
        }
    }
}

async fn cli_log(
    State(state): State<SharedState>,
    AxumPath((kind, slug)): AxumPath<(String, String)>,
) -> impl IntoResponse {
    match cli_job_dir(&state, &kind, &slug) {
        Ok(dir) => (
            StatusCode::OK,
            [
                (header::CONTENT_TYPE, "application/octet-stream"),
                (header::CACHE_CONTROL, "no-store"),
            ],
            terminal::tail_log(&dir.join("terminal.log")),
        )
            .into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

async fn cli_stop(
    State(state): State<SharedState>,
    AxumPath((kind, slug)): AxumPath<(String, String)>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return (
            StatusCode::FORBIDDEN,
            JsonResponse(json!({ "error": "只允许本地页面停止任务" })),
        )
            .into_response();
    }
    let dir = match cli_job_dir(&state, &kind, &slug) {
        Ok(dir) => dir,
        Err(error) => return err_response(error).into_response(),
    };
    let Some(session) = state.sessions.get(&dir) else {
        return err_response(anyhow::anyhow!("终端会话已结束")).into_response();
    };
    match session.stop(&dir) {
        Ok(()) => JsonResponse(json!({ "status": "stopping" })).into_response(),
        Err(error) => err_response(error).into_response(),
    }
}

/// 让本机 CLI 自己读 SKILL.md 按流程做，不依赖 skill 自动发现/匹配（用户选了不把这两个 skill
/// 装进 ~/.claude/skills/，避免变成全局配置副作用）。
fn skill_prompt_header(skill_dir: &std::path::Path) -> String {
    format!(
        "请读取并严格执行 {dir}/SKILL.md 里描述的完整工作流——把该文件里提到的 SKILL_DIR 直接当成 \
         {dir}，所有 scripts/ 和 references/ 相对路径都基于这个目录解析。\n\n",
        dir = skill_dir.display()
    )
}

/// 复制到外部终端时使用的等价命令；内置终端以结构化参数启动，不调用 shell。
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn build_command(runner: Runner, prompt: &str, cwd: &std::path::Path) -> String {
    let prefix = match runner {
        Runner::Codex => format!(
            "codex --no-alt-screen -C {} -s workspace-write -a on-request",
            shell_quote(&cwd.display().to_string())
        ),
        Runner::Manual | Runner::Claude => "claude --permission-mode manual".to_string(),
        Runner::Api => "Quant Lab 自定义 API 运行器".to_string(),
    };
    format!(
        "cd {} && {prefix} {}",
        shell_quote(&cwd.display().to_string()),
        shell_quote(prompt)
    )
}

fn validate_api_selection(
    state: &AppState,
    runner: Runner,
    profile_id: Option<&str>,
) -> anyhow::Result<()> {
    if runner != Runner::Api {
        return Ok(());
    }
    let id = profile_id
        .filter(|id| !id.is_empty())
        .ok_or_else(|| anyhow::anyhow!("请先在账号设置保存 API 服务商配置"))?;
    CliSettings::load(&state.cli_settings_path)?
        .profile(id)?
        .validate()
}

#[derive(Deserialize)]
struct CompanyAnalysisRequest {
    name: String,
    #[serde(default)]
    runner: Runner,
    profile_id: Option<String>,
}

async fn list_company_analysis(State(state): State<SharedState>) -> impl IntoResponse {
    let jobs = skill_runner::list_jobs(&state.company_analysis_dir, "deep_dive.html");
    JsonResponse(json!({ "jobs": jobs }))
}

async fn start_company_analysis(
    State(state): State<SharedState>,
    headers: HeaderMap,
    JsonResponse(req): JsonResponse<CompanyAnalysisRequest>,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return (
            StatusCode::FORBIDDEN,
            JsonResponse(json!({ "error": "只允许本地页面启动任务" })),
        )
            .into_response();
    }
    let name = req.name.trim().to_string();
    if name.is_empty() {
        return err_response(anyhow::anyhow!("公司名/代码不能为空")).into_response();
    }
    if let Err(error) = validate_api_selection(&state, req.runner, req.profile_id.as_deref()) {
        return err_response(error).into_response();
    }
    let slug = skill_runner::new_job_slug(&name);
    let job_dir = state.company_analysis_dir.join(&slug);
    if let Err(e) = std::fs::create_dir_all(&state.company_analysis_dir)
        .and_then(|_| std::fs::create_dir(&job_dir))
    {
        return err_response(anyhow::anyhow!("创建任务目录失败: {e}")).into_response();
    }
    let out_dir = std::fs::canonicalize(&job_dir).unwrap_or(job_dir.clone());
    let skill_dir = std::fs::canonicalize(&state.company_deep_dive_skill_dir)
        .unwrap_or_else(|_| state.company_deep_dive_skill_dir.clone());

    let prompt = format!(
        "{header}任务：对 \"{name}\" 做买入前的深度价值分析（11 维度打分 + DCF + HTML 决策仪表盘）。\n\
         本次研究的数据目录固定为 {out}/workflow。请按 SKILL.md 的真实阶段执行，并在每项工作完成后立刻把对应的有效 JSON 写入该目录；不要预先创建空文件或伪造进度：\n\
         数据采集：stock_quote.json、financial_statements.json；\n\
         多维分析：economic_model.json、pestel.json、porter.json、asset_valuation.json；\n\
         估值综合：dcf_valuation.json、analysis_summary.json。\n\
         调用 render_dashboard.py 时，--data-dir 指向 {out}/workflow，--analysis-json 指向 {out}/workflow/analysis_summary.json。\n\
         完整仪表盘存到 {out}/deep_dive.html（必须用 render_dashboard.py 生成，不要自己手写 HTML）；\n\
         另外在 {out}/summary.json 写一份精简摘要，字段：company, code, score_total, score_max, \
         recommendation_band, buy_price_range, margin_of_safety_pct, generated_at。",
        header = skill_prompt_header(&skill_dir),
        name = name,
        out = out_dir.display(),
    );
    let command = build_command(req.runner, &prompt, &state.trading_analyst_root);

    if let Err(e) = skill_runner::prepare_job(&job_dir, &command, &name) {
        return err_response(anyhow::anyhow!("准备任务失败: {e}")).into_response();
    }

    if req.runner != Runner::Manual {
        if let Err(error) = state.sessions.launch(
            req.runner,
            &job_dir,
            &state.trading_analyst_root,
            &prompt,
            "deep_dive.html",
            &state.cli_settings_path,
            req.profile_id.as_deref(),
        ) {
            let _ = terminal::write_status(&job_dir, "failed", req.runner, None);
            return err_response(error).into_response();
        }
    }

    (
        StatusCode::OK,
        JsonResponse(json!({ "slug": slug, "command": command, "runner": req.runner.name(),
            "status": if req.runner == Runner::Manual { "waiting_for_manual_run" } else { "running" } })),
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
    match skill_runner::read_report_html(&state.company_analysis_dir.join(&slug), "deep_dive.html")
    {
        Ok(html) => Html(html).into_response(),
        Err(e) => err_response(anyhow::anyhow!("报告还没生成或读取失败: {e}")).into_response(),
    }
}

#[derive(Deserialize)]
struct TradeReviewRequest {
    symbol: String,
    #[serde(default)]
    runner: Runner,
    profile_id: Option<String>,
}

async fn list_trade_review(State(state): State<SharedState>) -> impl IntoResponse {
    let jobs = skill_runner::list_jobs(&state.trade_review_dir, "review.html");
    JsonResponse(json!({ "jobs": jobs }))
}

async fn start_trade_review(
    State(state): State<SharedState>,
    headers: HeaderMap,
    JsonResponse(req): JsonResponse<TradeReviewRequest>,
) -> impl IntoResponse {
    if !local_origin(&headers) {
        return (
            StatusCode::FORBIDDEN,
            JsonResponse(json!({ "error": "只允许本地页面启动任务" })),
        )
            .into_response();
    }
    let symbol = req.symbol.trim().to_string();
    if symbol.is_empty() {
        return err_response(anyhow::anyhow!("symbol 不能为空")).into_response();
    }
    if let Err(error) = validate_api_selection(&state, req.runner, req.profile_id.as_deref()) {
        return err_response(error).into_response();
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
        .and_then(|arr| {
            arr.iter()
                .find(|s| s["symbol"].as_str() == Some(symbol.as_str()))
        })
        .cloned()
        .unwrap_or(Value::Null);
    let live = match Credentials::load(&state.credentials_path) {
        Ok(credentials) => live_trading::snapshot(&credentials)
            .await
            .unwrap_or(json!({ "positions": [], "today_orders": [] })),
        Err(_) => json!({ "positions": [], "today_orders": [] }),
    };
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
    if let Err(e) =
        std::fs::create_dir_all(&state.trade_review_dir).and_then(|_| std::fs::create_dir(&job_dir))
    {
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
         为了让应用内工作流看板显示真实进度，每项完成后立刻在 {out}/workflow 写入对应的有效 JSON，不要创建空文件或伪造进度：\n\
         market_snapshot.json（最新行情、技术指标及数据时间）、news_context.json（新增资讯和来源）、\
         review_analysis.json（实际操作、合理/不合理之处、风险和下一步调整）。\n\
         复盘 HTML 存到 {out}/review.html；另外在 {out}/summary.json 写精简摘要，字段：\n\
         symbol, verdict(on_track|watch|off_track), summary, next_actions, generated_at。",
        header = skill_prompt_header(&skill_dir),
        symbol = symbol,
        out = out_dir.display(),
    );
    let command = build_command(req.runner, &prompt, &state.trading_analyst_root);

    if let Err(e) = skill_runner::prepare_job(&job_dir, &command, &symbol) {
        return err_response(anyhow::anyhow!("准备任务失败: {e}")).into_response();
    }

    if req.runner != Runner::Manual {
        if let Err(error) = state.sessions.launch(
            req.runner,
            &job_dir,
            &state.trading_analyst_root,
            &prompt,
            "review.html",
            &state.cli_settings_path,
            req.profile_id.as_deref(),
        ) {
            let _ = terminal::write_status(&job_dir, "failed", req.runner, None);
            return err_response(error).into_response();
        }
    }

    (
        StatusCode::OK,
        JsonResponse(json!({ "slug": slug, "command": command, "runner": req.runner.name(),
            "status": if req.runner == Runner::Manual { "waiting_for_manual_run" } else { "running" } })),
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
        let lib = FactorLib::load("auto-trader/strategy-kit/strategy_kit.json").unwrap();
        let script = lib.render_paired("rsi_14", 100, 100000.0).unwrap();
        println!("{script}");
        assert!(script.contains("plot(rsiValue"));
        assert!(script.contains("plot(close"));

        let combo = lib
            .compose("breakout", "bearish_divergence", 100, 100000.0)
            .unwrap();
        println!("{combo}");
        assert!(combo.contains("plot(macdHist"));
        assert!(combo.contains("plot(bbUpper"));
    }
}
