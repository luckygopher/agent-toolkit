package imagegen

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func testApp(t *testing.T) (*App, map[string]string) {
	t.Helper()
	home := t.TempDir()
	env := map[string]string{"IMAGE_GEN_CONFIG_PATH": filepath.Join(home, "config.json"), "CODEX_HOME": filepath.Join(home, "codex")}
	app := &App{Version: "0.3.0-test", Home: home, Getenv: func(key string) string { return env[key] }, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	return app, env
}
func writeConfig(t *testing.T, a *App, base string) Config {
	t.Helper()
	cfg := Config{base, "dummy-private-key", DefaultModel, "1024x1024", 300, a.DefaultOutput()}
	if err := a.Save(cfg, false); err != nil {
		t.Fatal(err)
	}
	return cfg
}
func requireFailure(t *testing.T, err error, code string) {
	t.Helper()
	failure, ok := err.(*Failure)
	if !ok || failure.Code != code {
		t.Fatalf("expected %s, got %v", code, err)
	}
}

func TestConfigIndependentAndPrivate(t *testing.T) {
	a, _ := testApp(t)
	cfg := writeConfig(t, a, "https://images.invalid")
	loaded, sources, err := a.Load(true)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.BaseURL != "https://images.invalid/v1" || loaded.APIKey != cfg.APIKey || sources["model"] != "config" {
		t.Fatal("settings not preserved")
	}
	if runtime.GOOS != "windows" {
		info, _ := os.Stat(a.ConfigPath())
		if info.Mode().Perm() != 0600 {
			t.Fatalf("permissions %o", info.Mode().Perm())
		}
	}
	result, err := a.Status()
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(result)
	if bytes.Contains(data, []byte(cfg.APIKey)) {
		t.Fatal("status disclosed key")
	}
	if result.(map[string]any)["runtime"] != "native" {
		t.Fatal("not native")
	}
}

func TestConfigOverwriteRequiresExplicitChoice(t *testing.T) {
	a, _ := testApp(t)
	cfg := writeConfig(t, a, "https://images.invalid")
	before, _ := os.ReadFile(a.ConfigPath())
	cfg.Model = "different"
	requireFailure(t, a.Save(cfg, false), "config_exists")
	after, _ := os.ReadFile(a.ConfigPath())
	if !bytes.Equal(before, after) {
		t.Fatal("existing config overwritten")
	}
	if err := a.Save(cfg, true); err != nil {
		t.Fatal(err)
	}
	got, _, _ := a.Load(true)
	if got.Model != "different" {
		t.Fatal("explicit replacement failed")
	}
}

func TestEnvironmentOverridesSavedConfig(t *testing.T) {
	a, env := testApp(t)
	writeConfig(t, a, "https://saved.invalid")
	env["IMAGE_GEN_BASE_URL"] = "https://override.invalid/custom/v1"
	env["IMAGE_GEN_MODEL"] = "another-model"
	env["IMAGE_GEN_API_KEY"] = "override-dummy"
	got, sources, err := a.Load(true)
	if err != nil {
		t.Fatal(err)
	}
	if got.BaseURL != env["IMAGE_GEN_BASE_URL"] || got.APIKey != "override-dummy" || sources["api_key"] != "IMAGE_GEN_API_KEY" {
		t.Fatal("override failed")
	}
}

func TestConfigValidationBeforeWrite(t *testing.T) {
	for name, change := range map[string]func(*Config){"key": func(c *Config) { c.APIKey = "bad\nkey" }, "model": func(c *Config) { c.Model = "" }, "size": func(c *Config) { c.Size = "4096x4096" }, "timeout": func(c *Config) { c.Timeout = 1 }} {
		t.Run(name, func(t *testing.T) {
			a, _ := testApp(t)
			cfg := Config{"https://images.invalid", "dummy-key", DefaultModel, "1024x1024", 300, a.DefaultOutput()}
			change(&cfg)
			if a.Save(cfg, false) == nil {
				t.Fatal("invalid setting accepted")
			}
			if _, err := os.Stat(a.ConfigPath()); !os.IsNotExist(err) {
				t.Fatal("invalid settings written")
			}
		})
	}
}

func TestNormalizeAddresses(t *testing.T) {
	for input, want := range map[string]string{"https://example.invalid": "https://example.invalid/v1", "https://example.invalid/v1/images/generations": "https://example.invalid/v1", "https://example.invalid/proxy/v1/": "https://example.invalid/proxy/v1", "http://127.0.0.1:1234": "http://127.0.0.1:1234/v1"} {
		got, err := normalizeBase(input)
		if err != nil || got != want {
			t.Fatalf("%s => %s %v", input, got, err)
		}
	}
	for _, input := range []string{"http://remote.invalid/v1", "https://user:password@images.invalid/v1", "https://images.invalid/v1?key=secret", "not-url"} {
		if _, err := normalizeBase(input); err == nil {
			t.Fatalf("accepted %s", input)
		}
	}
}

func TestCodexImportPreservesChatAndImageModel(t *testing.T) {
	a, env := testApp(t)
	root := env["CODEX_HOME"]
	_ = os.MkdirAll(root, 0700)
	chat := []byte("model='chat-model'\nmodel_provider='relay'\n[model_providers.relay]\nbase_url='https://images.invalid/v1'\nrequires_openai_auth=true\n")
	auth := []byte(`{"OPENAI_API_KEY":"imported-dummy","tokens":{"access_token":"oauth-never-use"}}`)
	_ = os.WriteFile(filepath.Join(root, "config.toml"), chat, 0600)
	_ = os.WriteFile(filepath.Join(root, "auth.json"), auth, 0600)
	result, err := a.importedConfig("image-model", false)
	if err != nil {
		t.Fatal(err)
	}
	if result.(map[string]any)["model"] != "image-model" {
		t.Fatal("chat model imported")
	}
	loaded, _, _ := a.Load(true)
	if loaded.APIKey != "imported-dummy" {
		t.Fatal("wrong credential")
	}
	for file, expected := range map[string][]byte{"config.toml": chat, "auth.json": auth} {
		data, _ := os.ReadFile(filepath.Join(root, file))
		if !bytes.Equal(data, expected) {
			t.Fatal("chat file changed")
		}
	}
}

func TestCodexOAuthIsNotAnAPIKey(t *testing.T) {
	a, env := testApp(t)
	root := env["CODEX_HOME"]
	_ = os.MkdirAll(root, 0700)
	_ = os.WriteFile(filepath.Join(root, "auth.json"), []byte(`{"tokens":{"access_token":"oauth-never-use"}}`), 0600)
	_, _, err := a.CodexCredentials()
	requireFailure(t, err, "not_configured")
}

func TestCodexProfileAndProviderEnvironmentKey(t *testing.T) {
	a, env := testApp(t)
	root := env["CODEX_HOME"]
	_ = os.MkdirAll(root, 0700)
	env["RELAY_DUMMY_KEY"] = "right-provider"
	_ = os.WriteFile(filepath.Join(root, "config.toml"), []byte("profile='selected'\nmodel_provider='other'\n[profiles.selected]\nmodel_provider='relay'\n[model_providers.relay]\nbase_url='https://images.invalid/custom/v1'\nenv_key='RELAY_DUMMY_KEY'\n"), 0600)
	base, key, err := a.CodexCredentials()
	if err != nil || base != "https://images.invalid/custom/v1" || key != "right-provider" {
		t.Fatalf("import: %s %v", base, err)
	}
}

func TestCLIArgumentCompatibility(t *testing.T) {
	options, err := parse([]string{"edit", "change background", "reference.png", "--mask", "mask.png", "--size", "1536x1024", "--count", "2"})
	if err != nil || options.Prompt != "change background" || len(options.References) != 1 || options.Mask != "mask.png" || options.Count != 2 {
		t.Fatalf("options %#v %v", options, err)
	}
	options, err = parse([]string{"edit", "--prompt-file", "prompt.txt", "--reference", "reference.png"})
	if err != nil || options.PromptFile != "prompt.txt" || len(options.References) != 1 {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"generate", "one", "two"}, {"generate", "--count", "no-number"}, {"generate", "one", "--quality", "unknown"}, {"edit", "positional", "--prompt-file", "file"}} {
		if _, err := parse(args); err == nil {
			t.Fatalf("accepted %#v", args)
		}
	}
}

func TestNativeVersionDoesNotRequireConfiguration(t *testing.T) {
	a, _ := testApp(t)
	if a.Run([]string{"--version"}) != 0 {
		t.Fatal("version failed")
	}
	if !strings.Contains(a.Out.(*bytes.Buffer).String(), `"runtime": "native"`) {
		t.Fatal("version did not identify native runtime")
	}
}
