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
        }))
    }

    pub fn provider(&self, runner: Runner) -> Option<&ProviderSettings> {
        match runner {
            Runner::Codex => Some(&self.codex),
            Runner::Claude => Some(&self.claude),
            Runner::Manual => None,
        }
    }

    pub fn update(path: &Path, runner: Runner, update: ProviderUpdate) -> Result<Value> {
        let mut settings = Self::load(path)?;
        let provider = match runner {
            Runner::Codex => &mut settings.codex,
            Runner::Claude => &mut settings.claude,
            Runner::Manual => bail!("手动模式无需 API 配置"),
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
