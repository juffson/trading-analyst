//! 策略片段库读取 + 渲染。
//!
//! 数据来自 data/strategy_kit.json（从 ../trading-analyst/auto-trader/strategy-kit/
//! 复制的一份种子拷贝——quant-studio 是独立工具，不跨目录依赖那个 Python 项目，
//! 两边各自维护，暂时靠手动同步）。渲染逻辑（dedupe setup 行、拼装 input 声明、
//! 组合 entry/exit）和 strategy_kit.py 保持一致。
use std::collections::HashMap;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result, anyhow};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct InputDef {
    pub var: String,
    pub label: String,
    pub default: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Factor {
    pub name: String,
    pub direction: String,
    #[serde(default)]
    pub groups: Vec<String>,
    pub description: String,
    #[serde(default)]
    pub implemented: bool,
    #[serde(default)]
    pub pair_with: Option<String>,
    #[serde(default)]
    pub inputs: Vec<InputDef>,
    #[serde(default)]
    pub setup: Vec<String>,
    #[serde(default)]
    pub condition: String,
    #[serde(default)]
    pub note: Option<String>,
}

#[derive(Debug, Deserialize)]
struct FactorLibFile {
    blocks: HashMap<String, Factor>,
}

pub struct FactorLib {
    pub blocks: HashMap<String, Factor>,
}

impl FactorLib {
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let text = fs::read_to_string(&path)
            .with_context(|| format!("读取因子库失败: {}", path.as_ref().display()))?;
        let file: FactorLibFile = serde_json::from_str(&text).context("解析 strategy_kit.json 失败")?;
        Ok(Self { blocks: file.blocks })
    }

    pub fn get(&self, id: &str) -> Result<&Factor> {
        self.blocks
            .get(id)
            .ok_or_else(|| anyhow!("未知片段 id: {id}"))
    }

    /// 找出 `base_name` 对应的一对 (long_id, short_id)。
    ///
    /// 库里有两种配对写法，都要支持：
    /// 1. **后缀配对**——`rsi_14_long` / `rsi_14_short`，base_name 是去掉后缀的公共前缀。
    /// 2. **`pair_with` 显式配对**——`breakout` ↔ `bearish_divergence`，两个 id 毫无字面关系，
    ///    只能顺着 `pair_with` 字段找对家。这类 base_name 传任意一边的 id 都认，
    ///    统一归一到 long 那一边（见 `paired_key`），这样 performance jsonl 的文件名才稳定。
    pub fn resolve_pair(&self, base_name: &str) -> Result<(String, String)> {
        let long_id = format!("{base_name}_long");
        let short_id = format!("{base_name}_short");
        if self.blocks.contains_key(&long_id) && self.blocks.contains_key(&short_id) {
            return Ok((long_id, short_id));
        }

        let this = self.get(base_name)?;
        let mate_id = this
            .pair_with
            .as_deref()
            .ok_or_else(|| anyhow!("{base_name} 既没有 _long/_short 后缀配对，也没有 pair_with"))?;
        let mate = self.get(mate_id)?;
        if this.direction == mate.direction {
            anyhow::bail!("{base_name} 和 {mate_id} 方向相同（都是 {}），配不成开仓/平仓", this.direction);
        }
        Ok(if this.direction == "long" {
            (base_name.to_string(), mate_id.to_string())
        } else {
            (mate_id.to_string(), base_name.to_string())
        })
    }

    /// 配对的规范 key——固定取 long 那一边，且后缀配对时去掉 `_long`。
    /// performance jsonl 用它当文件名，所以同一对因子不管从哪一边点进来都要落到同一个 key。
    pub fn paired_key(&self, base_name: &str) -> Result<String> {
        let (long_id, _) = self.resolve_pair(base_name)?;
        Ok(long_id.strip_suffix("_long").unwrap_or(&long_id).to_string())
    }

    /// 配对成一个「long 开仓 / short 平仓」的完整策略。
    pub fn render_paired(&self, base_name: &str, qty: i64, capital: f64) -> Result<String> {
        let (long_id, short_id) = self.resolve_pair(base_name)?;
        let long_f = self.get(&long_id)?;
        let short_f = self.get(&short_id)?;
        if !(long_f.implemented && short_f.implemented) {
            anyhow::bail!("{long_id} / {short_id} 里有一个 implemented=false");
        }
        let key = long_id.strip_suffix("_long").unwrap_or(&long_id);
        Ok(build_script(key, long_f, short_f, qty, capital))
    }

    /// 任意两个片段：entry 开仓 / exit 平仓。
    pub fn compose(&self, entry_id: &str, exit_id: &str, qty: i64, capital: f64) -> Result<String> {
        let entry_f = self.get(entry_id)?;
        let exit_f = self.get(exit_id)?;
        if !(entry_f.implemented && exit_f.implemented) {
            anyhow::bail!("{entry_id} 或 {exit_id} 的 implemented=false");
        }
        let name = format!("{entry_id}_entry_{exit_id}_exit");
        Ok(build_script(&name, entry_f, exit_f, qty, capital))
    }
}

fn dedupe(lines: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for line in lines {
        if seen.insert(line.clone()) {
            out.push(line);
        }
    }
    out
}

fn render_inputs(inputs: &[InputDef]) -> Vec<String> {
    inputs
        .iter()
        .map(|inp| {
            let is_float = inp.default.is_f64() && !inp.default.is_i64() && !inp.default.is_u64();
            let kind = if is_float { "float" } else { "int" };
            format!(
                r#"{} = input.{}({}, title="{}")"#,
                inp.var, kind, inp.default, inp.label
            )
        })
        .collect()
}

fn build_script(strategy_name: &str, entry: &Factor, exit: &Factor, qty: i64, capital: f64) -> String {
    let inputs = dedupe(
        render_inputs(&entry.inputs)
            .into_iter()
            .chain(render_inputs(&exit.inputs)),
    );
    let setup = dedupe(entry.setup.iter().cloned().chain(exit.setup.iter().cloned()));

    let (long_or_short, position_id) = if entry.direction == "long" {
        ("strategy.long", "Long")
    } else {
        ("strategy.short", "Short")
    };

    let mut lines = vec![format!(
        r#"//@version=6
strategy("{strategy_name}", overlay=false, initial_capital={capital})"#
    )];
    lines.extend(inputs);
    lines.extend(setup.iter().cloned());
    lines.push(format!("if {}", entry.condition));
    lines.push(format!(
        r#"    strategy.entry("{position_id}", {long_or_short}, qty={qty})"#
    ));
    lines.push(format!("if {}", exit.condition));
    lines.push(format!(r#"    strategy.close("{position_id}")"#));
    // 保证每份生成的脚本都会 plot 收盘价，这样 quant-studio 不用单独接
    // QuoteContext/WebSocket 拉K线——图表的价格线直接从这次回测的 chart_json 里取。
    lines.push(r#"plot(close, title="Close")"#.to_string());
    // strategy_kit.json 里的 setup 只算指标值，不含 plot() ——auto-trader 那边不需要看图，
    // 只读 report_json 就够。quant-studio 是给人看图的，所以这里把每个 setup 算出来的变量
    // 名都自动 plot 一遍，这样指标线才会出现在 chart_json.seriesGraphs 里。
    for var in setup.iter().flat_map(|line| extract_setup_vars(line)) {
        lines.push(format!(r#"plot({var}, title="{var}")"#));
    }

    lines.join("\n") + "\n"
}

/// 从一行 setup（形如 `rsiValue = ta.rsi(...)` 或 `[macdLine, macdSignalLine, macdHist] = ...`）
/// 里取出左边声明的变量名。
fn extract_setup_vars(line: &str) -> Vec<String> {
    let Some((lhs, _)) = line.split_once('=') else {
        return Vec::new();
    };
    let lhs = lhs.trim();
    if let Some(inner) = lhs.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
        inner.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
    } else {
        vec![lhs.to_string()]
    }
}
