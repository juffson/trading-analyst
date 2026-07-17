//! 因子"持续跟进效果"的本地记录——每次跑一次回测，追加一条快照到
//! data/performance/<factor_key>.jsonl，用来看同一个因子在不同时间点测出来的表现有没有漂移。
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

use anyhow::{Context, Result};
use chrono::Utc;
use serde_json::{Value, json};

pub struct PerformanceLog {
    dir: PathBuf,
}

impl PerformanceLog {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        Self { dir: dir.into() }
    }

    fn path_for(&self, factor_key: &str) -> PathBuf {
        // factor_key 可能含 "/"（比如 "breakout+bearish_divergence"），用它本身当文件名，
        // 不含路径分隔符字符，直接拼即可。
        self.dir.join(format!("{factor_key}.jsonl"))
    }

    pub fn append(&self, factor_key: &str, symbol: &str, metrics: &Value) -> Result<()> {
        fs::create_dir_all(&self.dir).context("创建 performance 目录失败")?;
        let record = json!({
            "ts": Utc::now().to_rfc3339(),
            "symbol": symbol,
            "metrics": metrics,
        });
        let path = self.path_for(factor_key);
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .with_context(|| format!("打开 {} 失败", path.display()))?;
        writeln!(file, "{record}").context("写入 performance 记录失败")?;
        Ok(())
    }

    pub fn history(&self, factor_key: &str) -> Result<Vec<Value>> {
        let path = self.path_for(factor_key);
        if !path.exists() {
            return Ok(Vec::new());
        }
        let text = fs::read_to_string(&path).with_context(|| format!("读取 {} 失败", path.display()))?;
        Ok(text
            .lines()
            .filter(|l| !l.trim().is_empty())
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect())
    }
}

/// factor_key 会被拼进本地文件路径——只放行字母数字和 `_.+-`，挡掉路径穿越
/// （`..`/`/` 之类），因为这个 key 来自 API 请求参数，虽然是本地个人工具，也不该
/// 让请求方随便控制往哪个文件写。
pub fn is_safe_key(key: &str) -> bool {
    !key.is_empty()
        && key
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '+' | '-'))
}
