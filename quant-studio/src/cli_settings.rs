use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};
use url::Url;

use crate::terminal::Runner;

#[derive(Clone, Default, Deserialize, Serialize)]
pub struct ProviderSettings {
    pub enabled: bool,
    pub endpoint: String,
    pub api_key: String,
    pub model: String,
    #[serde(default)]
    pub auth_header: String,
}

#[derive(Default, Deserialize, Serialize)]
pub struct CliSettings {
    pub codex: ProviderSettings,
    pub claude: ProviderSettings,
    #[serde(default)]
    pub profiles: Vec<ApiProfile>,
}

#[derive(Clone, Deserialize, Serialize)]
pub struct ApiProfile {
    pub id: String,
    pub name: String,
    pub endpoint: String,
    pub protocol: String,
    pub api_key: String,
    pub model: String,
    #[serde(default)]
    pub auth_header: String,
}

#[derive(Clone, Deserialize)]
pub struct ApiProfileUpdate {
    pub id: String,
    pub name: String,
    pub endpoint: String,
    pub protocol: String,
    pub api_key: Option<String>,
    pub model: String,
    #[serde(default)]
    pub auth_header: String,
}

#[derive(Deserialize)]
pub struct ProviderUpdate {
    pub enabled: Option<bool>,
    pub endpoint: Option<String>,
    pub api_key: Option<String>,
    pub model: Option<String>,
    pub auth_header: Option<String>,
    #[serde(default)]
    pub reset: bool,
}

impl CliSettings {
    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        serde_json::from_slice(&fs::read(path).context("读取 AI CLI 配置失败")?)
            .context("解析 AI CLI 配置失败")
    }

    pub fn status(path: &Path) -> Result<Value> {
        let settings = Self::load(path)?;
        Ok(json!({
            "codex": settings.codex.public_status(),
            "claude": settings.claude.public_status(),
            "profiles": settings.profiles.iter().map(ApiProfile::public_status).collect::<Vec<_>>(),
        }))
    }

    pub fn profile(&self, id: &str) -> Result<ApiProfile> {
        self.profiles
            .iter()
            .find(|profile| profile.id == id)
            .cloned()
            .context("找不到所选 API 服务商，请到账号设置重新选择")
    }

    pub fn resolve_profile(&self, update: ApiProfileUpdate) -> Result<ApiProfile> {
        let old_key = self
            .profiles
            .iter()
            .find(|profile| profile.id == update.id)
            .map(|profile| profile.api_key.as_str())
            .unwrap_or("");
        let profile = ApiProfile {
            id: update.id.trim().to_string(),
            name: update.name.trim().to_string(),
            endpoint: update.endpoint.trim().trim_end_matches('/').to_string(),
            protocol: update.protocol,
            api_key: update
                .api_key
                .as_deref()
                .map(str::trim)
                .filter(|key| !key.is_empty())
                .unwrap_or(old_key)
                .to_string(),
            model: update.model.trim().to_string(),
            auth_header: update.auth_header,
        };
        profile.validate()?;
        Ok(profile)
    }

    pub fn upsert_profile(path: &Path, update: ApiProfileUpdate) -> Result<Value> {
        let mut settings = Self::load(path)?;
        let profile = settings.resolve_profile(update)?;
        settings.profiles.retain(|item| item.id != profile.id);
        settings.profiles.push(profile);
        save(path, &settings)?;
        Self::status(path)
    }

    pub fn delete_profile(path: &Path, id: &str) -> Result<Value> {
        let mut settings = Self::load(path)?;
        let count = settings.profiles.len();
        settings.profiles.retain(|profile| profile.id != id);
        if settings.profiles.len() == count {
            bail!("服务商配置不存在");
        }
        save(path, &settings)?;
        Self::status(path)
    }

    pub fn provider(&self, runner: Runner) -> Option<&ProviderSettings> {
        match runner {
            Runner::Codex => Some(&self.codex),
            Runner::Claude => Some(&self.claude),
            Runner::Manual | Runner::Api => None,
        }
    }

    pub fn update(path: &Path, runner: Runner, update: ProviderUpdate) -> Result<Value> {
        let mut settings = Self::load(path)?;
        let provider = match runner {
            Runner::Codex => &mut settings.codex,
            Runner::Claude => &mut settings.claude,
            Runner::Manual | Runner::Api => bail!("此模式不使用 CLI 配置"),
        };
        if update.reset {
            *provider = ProviderSettings::default();
        } else {
            if let Some(enabled) = update.enabled {
                provider.enabled = enabled;
            }
            if let Some(value) = update.endpoint {
                provider.endpoint = value.trim().to_string();
            }
            if let Some(value) = update.api_key.filter(|value| !value.trim().is_empty()) {
                provider.api_key = value.trim().to_string();
            }
            if let Some(value) = update.model {
                provider.model = value.trim().to_string();
            }
            if let Some(value) = update.auth_header {
                provider.auth_header = value;
            }
            provider.validate(runner)?;
        }
        save(path, &settings)?;
        Self::status(path)
    }
}

impl ProviderSettings {
    fn public_status(&self) -> Value {
        json!({
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "model": self.model,
            "has_key": !self.api_key.is_empty(),
            "auth_header": if self.auth_header.is_empty() { "api_key" } else { &self.auth_header },
        })
    }

    pub fn validate(&self, runner: Runner) -> Result<()> {
        if !self.enabled {
            return Ok(());
        }
        if self.endpoint.is_empty() || self.api_key.is_empty() {
            bail!("自定义 API 需要 Endpoint 和 Key");
        }
        let url = Url::parse(&self.endpoint).context("Endpoint 不是有效 URL")?;
        let loopback = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
        if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
            bail!("Endpoint 需要使用 HTTPS；本机 localhost 可使用 HTTP");
        }
        if url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            bail!("Endpoint 不能包含账号、查询参数或片段");
        }
        if self.endpoint.chars().any(char::is_control) || self.api_key.chars().any(char::is_control)
        {
            bail!("Endpoint 和 Key 不能包含控制字符");
        }
        if self.model.chars().any(char::is_control) {
            bail!("模型名不能包含控制字符");
        }
        if runner == Runner::Codex && self.model.is_empty() {
            bail!("Codex 自定义 API 需要填写模型名");
        }
        if runner == Runner::Claude
            && !matches!(self.auth_header.as_str(), "" | "api_key" | "bearer")
        {
            bail!("Claude Key 类型只能是 API Key 或 Bearer Token");
        }
        Ok(())
    }
}

impl ApiProfile {
    pub fn public_status(&self) -> Value {
        json!({
            "id": self.id,
            "name": self.name,
            "endpoint": self.endpoint,
            "protocol": self.protocol,
            "model": self.model,
            "auth_header": self.auth_header,
            "has_key": !self.api_key.is_empty(),
        })
    }

    pub fn validate(&self) -> Result<()> {
        if self.id.is_empty()
            || self.id.len() > 64
            || !self
                .id
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-'))
        {
            bail!("Provider ID 只能包含字母、数字、下划线或连字符，最多 64 位");
        }
        if self.name.is_empty() || self.name.len() > 80 || self.name.chars().any(char::is_control) {
            bail!("请填写有效的服务商名称");
        }
        if !matches!(
            self.protocol.as_str(),
            "responses" | "anthropic" | "chat_completions"
        ) {
            bail!("不支持的 API 协议");
        }
        if self.model.is_empty()
            || self.model.len() > 128
            || self.model.chars().any(char::is_control)
        {
            bail!("请填写服务商支持的模型名");
        }
        if self.api_key.is_empty() || self.api_key.chars().any(char::is_control) {
            bail!("请填写有效的 API Key");
        }
        if self.protocol == "anthropic"
            && !matches!(self.auth_header.as_str(), "" | "api_key" | "bearer")
        {
            bail!("Anthropic 协议的凭据类型只能是 API Key 或 Bearer Token");
        }
        validate_endpoint(&self.endpoint)
    }
}

pub async fn test_profile(profile: &ApiProfile) -> Result<Value> {
    profile.validate()?;
    let suffix = match profile.protocol.as_str() {
        "chat_completions" => "chat/completions",
        "responses" => "responses",
        "anthropic" => "messages",
        _ => unreachable!(),
    };
    let base = profile.endpoint.trim_end_matches('/');
    let url = if profile.protocol == "anthropic" {
        if base.ends_with("/v1/messages") {
            base.to_string()
        } else {
            format!(
                "{}/{suffix}",
                if base.ends_with("/v1") {
                    base.to_string()
                } else {
                    format!("{base}/v1")
                }
            )
        }
    } else if base.ends_with(&format!("/{suffix}")) {
        base.to_string()
    } else {
        format!("{base}/{suffix}")
    };
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .redirect(reqwest::redirect::Policy::none())
        .build()?;
    let mut request = client.post(url);
    let body = match profile.protocol.as_str() {
        "chat_completions" => json!({
            "model": profile.model,
            "messages": [{"role": "user", "content": "Call the ping tool."}],
            "max_tokens": 64,
            "tools": [{"type": "function", "function": {"name": "ping", "description": "Connection test", "parameters": {"type": "object", "properties": {}}}}],
            "tool_choice": {"type": "function", "function": {"name": "ping"}}
        }),
        "responses" => json!({"model": profile.model, "input": "ping", "max_output_tokens": 16}),
        "anthropic" => {
            json!({"model": profile.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1})
        }
        _ => unreachable!(),
    };
    request = if profile.protocol == "anthropic" {
        let request = request.header("anthropic-version", "2023-06-01");
        if profile.auth_header == "bearer" {
            request.bearer_auth(&profile.api_key)
        } else {
            request.header("x-api-key", &profile.api_key)
        }
    } else {
        request.bearer_auth(&profile.api_key)
    };
    let response = request.json(&body).send().await.context("连接服务商失败")?;
    if !response.status().is_success() {
        bail!(
            "连接测试失败：HTTP {}（请检查 Endpoint、协议、模型和 Key）",
            response.status()
        );
    }
    let status = response.status().as_u16();
    if profile.protocol == "chat_completions" {
        let body: Value = response.json().await.context("解析服务商响应失败")?;
        let tool_calls = body
            .pointer("/choices/0/message/tool_calls")
            .and_then(Value::as_array);
        if !tool_calls.is_some_and(|calls| !calls.is_empty()) {
            bail!(
                "连接成功，但模型未返回工具调用；完整工作流需要支持 Chat Completions function calling"
            );
        }
    }
    Ok(json!({"ok": true, "protocol": profile.protocol, "status": status}))
}

fn validate_endpoint(endpoint: &str) -> Result<()> {
    let url = Url::parse(endpoint).context("Endpoint 不是有效 URL")?;
    let loopback = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
        bail!("Endpoint 需要使用 HTTPS；本机 localhost 可使用 HTTP");
    }
    if url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || endpoint.chars().any(char::is_control)
    {
        bail!("Endpoint 不能包含账号、查询参数、片段或控制字符");
    }
    Ok(())
}

fn save(path: &Path, settings: &CliSettings) -> Result<()> {
    let parent = path.parent().context("配置目录不存在")?;
    fs::create_dir_all(parent).context("创建配置目录失败")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    }
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let temp = path.with_extension(format!("json.tmp.{}.{nanos}", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temp).context("创建临时配置文件失败")?;
    file.write_all(&serde_json::to_vec(settings)?)?;
    file.sync_all()?;
    fs::rename(&temp, path).context("保存 AI CLI 配置失败")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_profiles_preserve_keys_without_exposing_them() {
        let dir = std::env::temp_dir().join(format!("quant-api-profile-{}", std::process::id()));
        let path = dir.join("cli.json");
        let update = ApiProfileUpdate {
            id: "deepseek".into(),
            name: "DeepSeek".into(),
            endpoint: "https://api.deepseek.com".into(),
            protocol: "chat_completions".into(),
            api_key: Some("private-test-key".into()),
            model: "deepseek-chat".into(),
            auth_header: "api_key".into(),
        };
        let status = CliSettings::upsert_profile(&path, update.clone()).unwrap();
        assert_eq!(status["profiles"][0]["has_key"], true);
        assert!(!status.to_string().contains("private-test-key"));
        CliSettings::upsert_profile(
            &path,
            ApiProfileUpdate {
                name: "Updated".into(),
                api_key: Some(String::new()),
                ..update
            },
        )
        .unwrap();
        let settings = CliSettings::load(&path).unwrap();
        assert_eq!(
            settings.profile("deepseek").unwrap().api_key,
            "private-test-key"
        );
        assert_eq!(settings.profile("deepseek").unwrap().name, "Updated");
        CliSettings::delete_profile(&path, "deepseek").unwrap();
        assert!(CliSettings::load(&path).unwrap().profiles.is_empty());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn custom_provider_keeps_secret_private_and_can_reset() {
        let dir =
            std::env::temp_dir().join(format!("quant-studio-cli-settings-{}", std::process::id()));
        let path = dir.join("cli.json");
        let update = ProviderUpdate {
            enabled: Some(true),
            endpoint: Some("https://gateway.example.com/v1".into()),
            api_key: Some("secret-for-test".into()),
            model: Some("gpt-test".into()),
            auth_header: None,
            reset: false,
        };
        let status = CliSettings::update(&path, Runner::Codex, update).unwrap();
        assert_eq!(status["codex"]["enabled"], true);
        assert_eq!(status["codex"]["has_key"], true);
        assert!(!status.to_string().contains("secret-for-test"));
        assert_eq!(
            CliSettings::load(&path).unwrap().codex.api_key,
            "secret-for-test"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        CliSettings::update(
            &path,
            Runner::Codex,
            ProviderUpdate {
                enabled: None,
                endpoint: None,
                api_key: None,
                model: None,
                auth_header: None,
                reset: true,
            },
        )
        .unwrap();
        assert!(CliSettings::load(&path).unwrap().codex.api_key.is_empty());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn rejects_unsafe_or_incomplete_endpoints() {
        let mut provider = ProviderSettings {
            enabled: true,
            endpoint: "http://remote.example/v1".into(),
            api_key: "key".into(),
            model: "model".into(),
            auth_header: String::new(),
        };
        assert!(provider.validate(Runner::Codex).is_err());
        provider.endpoint = "https://gateway.example/v1?token=secret".into();
        assert!(provider.validate(Runner::Codex).is_err());
        provider.endpoint = "http://127.0.0.1:8888/v1".into();
        assert!(provider.validate(Runner::Codex).is_ok());
        provider.endpoint = "http://[::1]:8888/v1".into();
        assert!(provider.validate(Runner::Codex).is_ok());
    }

    #[test]
    fn claude_can_clear_optional_model_without_replacing_key() {
        let dir = std::env::temp_dir().join(format!(
            "quant-studio-claude-settings-{}",
            std::process::id()
        ));
        let path = dir.join("cli.json");
        CliSettings::update(
            &path,
            Runner::Claude,
            ProviderUpdate {
                enabled: Some(true),
                endpoint: Some("https://api.anthropic.com".into()),
                api_key: Some("test-key".into()),
                model: Some("claude-test".into()),
                auth_header: Some("api_key".into()),
                reset: false,
            },
        )
        .unwrap();
        CliSettings::update(
            &path,
            Runner::Claude,
            ProviderUpdate {
                enabled: None,
                endpoint: None,
                api_key: Some(String::new()),
                model: Some(String::new()),
                auth_header: None,
                reset: false,
            },
        )
        .unwrap();
        let settings = CliSettings::load(&path).unwrap();
        assert_eq!(settings.claude.api_key, "test-key");
        assert!(settings.claude.model.is_empty());
        let _ = fs::remove_dir_all(dir);
    }
}
