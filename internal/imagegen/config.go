// Copyright (C) 2026 luckygopher
// SPDX-License-Identifier: GPL-3.0-only
package imagegen

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/pelletier/go-toml/v2"
)

const DefaultModel = "gpt-image-2"

type Failure struct{ Code, Message string }

func (e *Failure) Error() string      { return e.Message }
func fail(code, message string) error { return &Failure{code, message} }

type Config struct {
	BaseURL   string `json:"base_url"`
	APIKey    string `json:"api_key"`
	Model     string `json:"model"`
	Size      string `json:"size"`
	Timeout   int    `json:"timeout"`
	OutputDir string `json:"output_dir"`
}

type App struct {
	Version  string
	Home     string
	Getenv   func(string) string
	Out, Err io.Writer
}

func New(version string, out, errOut io.Writer) *App {
	home, _ := os.UserHomeDir()
	return &App{Version: version, Home: home, Getenv: os.Getenv, Out: out, Err: errOut}
}

func (a *App) Expand(path string) string {
	if path == "~" {
		return a.Home
	}
	if strings.HasPrefix(path, "~/") || strings.HasPrefix(path, "~\\") {
		return filepath.Join(a.Home, path[2:])
	}
	return path
}

func (a *App) ConfigPath() string {
	if value := a.Getenv("IMAGE_GEN_CONFIG_PATH"); value != "" {
		p, _ := filepath.Abs(a.Expand(value))
		return p
	}
	root := a.Getenv("XDG_CONFIG_HOME")
	if runtime.GOOS == "windows" {
		root = a.Getenv("APPDATA")
		if root == "" {
			root = filepath.Join(a.Home, "AppData", "Roaming")
		}
	} else if root == "" {
		root = filepath.Join(a.Home, ".config")
	}
	return filepath.Join(root, "agent-toolkit", "image-gen.json")
}

func (a *App) DefaultOutput() string {
	return filepath.Join(a.Home, "Pictures", "agent-toolkit", "image-gen")
}

func normalizeBase(value string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(value))
	if err != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return "", fail("invalid_config", "API 地址必须是完整 HTTP(S) 地址，不能包含账号、查询参数或片段。")
	}
	local := u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1" || u.Hostname() == "::1"
	if u.Scheme != "https" && !(u.Scheme == "http" && local) {
		return "", fail("invalid_config", "远程 API 必须使用 HTTPS；本机 localhost 可以使用 HTTP。")
	}
	u.Path = strings.TrimRight(u.Path, "/")
	for _, suffix := range []string{"/images/generations", "/images/edits"} {
		if strings.HasSuffix(u.Path, suffix) {
			u.Path = strings.TrimSuffix(u.Path, suffix)
			break
		}
	}
	if u.Path == "" {
		u.Path = "/v1"
	}
	u.RawPath = ""
	return u.String(), nil
}

func (a *App) Load(requireKey bool) (Config, map[string]string, error) {
	cfg := Config{Model: DefaultModel, Size: "1024x1024", Timeout: 300, OutputDir: a.DefaultOutput()}
	sources := map[string]string{}
	data, err := os.ReadFile(a.ConfigPath())
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return cfg, sources, fail("invalid_config", "无法读取本机生图配置。")
	}
	if len(data) > 0 {
		if json.Unmarshal(data, &cfg) != nil {
			return cfg, sources, fail("invalid_config", "生图配置不是有效 JSON 对象，或字段类型错误。")
		}
		var fields map[string]json.RawMessage
		_ = json.Unmarshal(data, &fields)
		for key := range fields {
			sources[key] = "config"
		}
	}
	fields := map[string]*string{"base_url": &cfg.BaseURL, "api_key": &cfg.APIKey, "model": &cfg.Model, "size": &cfg.Size, "output_dir": &cfg.OutputDir}
	for key, pointer := range fields {
		env := "IMAGE_GEN_" + strings.ToUpper(key)
		if value := a.Getenv(env); value != "" {
			*pointer = value
			sources[key] = env
		} else if sources[key] == "" {
			sources[key] = "default"
		}
	}
	if value := a.Getenv("IMAGE_GEN_TIMEOUT"); value != "" {
		cfg.Timeout, err = strconv.Atoi(value)
		if err != nil {
			return cfg, sources, fail("invalid_config", "超时必须是整数秒。")
		}
		sources["timeout"] = "IMAGE_GEN_TIMEOUT"
	} else if sources["timeout"] == "" {
		sources["timeout"] = "default"
	}
	if cfg.BaseURL != "" {
		cfg.BaseURL, err = normalizeBase(cfg.BaseURL)
		if err != nil {
			return cfg, sources, err
		}
	}
	if strings.ContainsAny(cfg.APIKey, "\r\n") {
		return cfg, sources, fail("invalid_config", "API Key 不能包含换行。")
	}
	if strings.TrimSpace(cfg.Model) == "" || cfg.Size == "" || cfg.OutputDir == "" || cfg.Timeout < 10 || cfg.Timeout > 1800 {
		return cfg, sources, fail("invalid_config", "模型、尺寸和输出目录不能为空，超时应在 10–1800 秒之间。")
	}
	if requireKey && (cfg.BaseURL == "" || cfg.APIKey == "") {
		return cfg, sources, fail("not_configured", "请先打开 Setup 配置图片 API 地址和 Key。")
	}
	return cfg, sources, nil
}

func (a *App) Status() (any, error) {
	cfg, sources, err := a.Load(false)
	if err != nil {
		return nil, err
	}
	return map[string]any{"configured": cfg.BaseURL != "" && cfg.APIKey != "", "config_path": a.ConfigPath(), "base_url": cfg.BaseURL, "api_key_configured": cfg.APIKey != "", "model": cfg.Model, "size": cfg.Size, "timeout": cfg.Timeout, "output_dir": cfg.OutputDir, "sources": sources, "version": a.Version, "runtime": "native"}, nil
}

func (a *App) Save(cfg Config, overwrite bool) error {
	path := a.ConfigPath()
	if _, err := os.Lstat(path); err == nil && !overwrite {
		return fail("config_exists", "生图配置已存在，请确认替换；现有配置没有修改。")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fail("config_write_error", "无法访问本机生图配置。")
	}
	base, err := normalizeBase(cfg.BaseURL)
	if err != nil {
		return err
	}
	cfg.BaseURL, cfg.Model, cfg.APIKey = base, strings.TrimSpace(cfg.Model), strings.TrimSpace(cfg.APIKey)
	if cfg.APIKey == "" || strings.ContainsAny(cfg.APIKey, "\r\n") {
		return fail("invalid_config", "API Key 不能为空且不能包含换行。")
	}
	if cfg.Model == "" {
		return fail("invalid_config", "图片模型不能为空。")
	}
	if cfg.Size, err = resolveSize(cfg.Size, cfg.Model); err != nil {
		return err
	}
	if cfg.Timeout < 10 || cfg.Timeout > 1800 {
		return fail("invalid_config", "超时必须在 10–1800 秒之间。")
	}
	if cfg.OutputDir == "" {
		cfg.OutputDir = a.DefaultOutput()
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return fail("config_write_error", "无法创建本机配置目录。")
	}
	data, _ := json.MarshalIndent(cfg, "", "  ")
	file, err := os.CreateTemp(filepath.Dir(path), ".image-gen-*")
	if err != nil {
		return fail("config_write_error", "无法创建本机配置文件。")
	}
	defer os.Remove(file.Name())
	if _, err = file.Write(append(data, '\n')); err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return fail("config_write_error", "无法写入本机配置。")
	}
	if !overwrite {
		// A hard link installs a new private file without overwriting a racing setup.
		err = os.Link(file.Name(), path)
	} else {
		err = replaceConfig(file.Name(), path)
	}
	if err != nil {
		return fail("config_write_error", "无法保存配置，文件可能已存在或没有写入权限。")
	}
	return nil
}

func (a *App) CodexCredentials() (string, string, error) {
	root := a.Getenv("CODEX_HOME")
	if root == "" {
		root = filepath.Join(a.Home, ".codex")
	}
	root = a.Expand(root)
	var saved map[string]any
	data, err := os.ReadFile(filepath.Join(root, "config.toml"))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", "", fail("invalid_config", "无法读取 Codex 配置。")
	}
	if len(data) > 0 && toml.Unmarshal(data, &saved) != nil {
		return "", "", fail("invalid_config", "Codex TOML 配置无法解析。")
	}
	if saved == nil {
		saved = map[string]any{}
	}
	if profile, ok := saved["profile"].(string); ok {
		profiles, _ := saved["profiles"].(map[string]any)
		chosen, _ := profiles[profile].(map[string]any)
		for key, value := range chosen {
			saved[key] = value
		}
	}
	providerID, _ := saved["model_provider"].(string)
	if providerID == "" {
		providerID = "openai"
	}
	providers, _ := saved["model_providers"].(map[string]any)
	provider, _ := providers[providerID].(map[string]any)
	base, _ := provider["base_url"].(string)
	if base == "" && providerID == "openai" {
		base = "https://api.openai.com/v1"
	}
	env, _ := provider["env_key"].(string)
	key := ""
	if env != "" {
		key = a.Getenv(env)
	}
	openaiAuth, _ := provider["requires_openai_auth"].(bool)
	if key == "" && (providerID == "openai" || openaiAuth) {
		var auth struct {
			APIKey string `json:"OPENAI_API_KEY"`
		}
		data, err := os.ReadFile(filepath.Join(root, "auth.json"))
		if err == nil {
			_ = json.Unmarshal(data, &auth)
			key = auth.APIKey
		}
	}
	if key == "" || base == "" {
		return "", "", fail("not_configured", "没有可导入的 Codex API 地址和 Key。不会读取 ChatGPT 登录 token，请手动配置图片服务。")
	}
	base, err = normalizeBase(base)
	return base, key, err
}

func encode(writer io.Writer, value any) {
	encoder := json.NewEncoder(writer)
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(value)
}
func safeError(err error, key string) map[string]any {
	message := err.Error()
	if key != "" {
		message = strings.ReplaceAll(message, key, "[REDACTED]")
	}
	result := map[string]any{"status": "error", "message": message}
	var failure *Failure
	if errors.As(err, &failure) {
		result["code"] = failure.Code
	}
	return result
}

func (a *App) importedConfig(model string, overwrite bool) (any, error) {
	base, key, err := a.CodexCredentials()
	if err != nil {
		return nil, err
	}
	if model == "" {
		model = DefaultModel
	}
	cfg := Config{base, key, model, "1024x1024", 300, a.DefaultOutput()}
	if err = a.Save(cfg, overwrite); err != nil {
		return nil, err
	}
	return map[string]any{"configured": true, "api_key_configured": true, "base_url": base, "model": model, "config_path": a.ConfigPath(), "codex_modified": false}, nil
}

func limitRead(reader io.Reader, maximum int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(reader, maximum+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > maximum {
		return nil, fail("response_too_large", fmt.Sprintf("数据超过 %d MiB 上限。", maximum/(1024*1024)))
	}
	return data, nil
}
