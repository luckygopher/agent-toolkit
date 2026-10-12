package imagegen

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func setupTest(t *testing.T, a *App) *setupSession {
	t.Helper()
	session, err := a.newSetup(time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(session.Close)
	return session
}
func setupRequest(t *testing.T, s *setupSession, route string, payload any, headers map[string]string) (int, []byte, http.Header) {
	t.Helper()
	method := http.MethodGet
	var body io.Reader
	if payload != nil {
		method = http.MethodPost
		data, _ := json.Marshal(payload)
		body = bytes.NewReader(data)
	}
	request, _ := http.NewRequest(method, s.origin+s.prefix+route, body)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Origin", s.origin)
	request.Header.Set("X-Setup-Token", s.token)
	for key, value := range headers {
		if key == "Host" {
			request.Host = value
		} else {
			request.Header.Set(key, value)
		}
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	data, _ := io.ReadAll(response.Body)
	return response.StatusCode, data, response.Header
}
func setupValues() map[string]any {
	return map[string]any{"base_url": "https://images.invalid/v1", "api_key": "dummy-private-key", "model": "image-model", "size": "1024x1024", "timeout": 300}
}

func TestEmbeddedSetupPageNeedsNoExternalAssets(t *testing.T) {
	a, _ := testApp(t)
	s := setupTest(t, a)
	status, data, headers := setupRequest(t, s, "", nil, nil)
	if status != 200 || !strings.Contains(string(data), "从 Codex 导入连接") || bytes.Contains(data, []byte("__NONCE__")) {
		t.Fatal("embedded page missing")
	}
	if headers.Get("Cache-Control") != "no-store" || headers.Get("X-Frame-Options") != "DENY" || !strings.Contains(headers.Get("Content-Security-Policy"), s.nonce) {
		t.Fatal("security headers missing")
	}
}

func TestSetupStateDoesNotReturnKey(t *testing.T) {
	a, _ := testApp(t)
	writeConfig(t, a, "https://images.invalid")
	s := setupTest(t, a)
	status, data, _ := setupRequest(t, s, "state", nil, nil)
	if status != 200 || bytes.Contains(data, []byte("dummy-private-key")) {
		t.Fatal("state leaked key")
	}
	var object map[string]any
	_ = json.Unmarshal(data, &object)
	if object["api_key_configured"] != true || object["existing_configuration"] != true {
		t.Fatal(object)
	}
}

func TestSetupSavePersistsAndAutomaticallyStops(t *testing.T) {
	a, _ := testApp(t)
	s := setupTest(t, a)
	status, data, _ := setupRequest(t, s, "save", setupValues(), nil)
	if status != 200 || bytes.Contains(data, []byte("dummy-private-key")) {
		t.Fatalf("save %s", data)
	}
	cfg, _, err := a.Load(true)
	if err != nil || cfg.Model != "image-model" {
		t.Fatal(err)
	}
	select {
	case <-s.done:
	case <-time.After(3 * time.Second):
		t.Fatal("setup did not stop")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cfg.APIKey != "" {
		t.Fatal("key retained after closing")
	}
}

func TestSetupImportDoesNotWriteUntilSaved(t *testing.T) {
	a, env := testApp(t)
	root := env["CODEX_HOME"]
	_ = os.MkdirAll(root, 0700)
	_ = os.WriteFile(filepath.Join(root, "config.toml"), []byte("model_provider='relay'\n[model_providers.relay]\nbase_url='https://imported.invalid/v1'\nrequires_openai_auth=true\n"), 0600)
	_ = os.WriteFile(filepath.Join(root, "auth.json"), []byte(`{"OPENAI_API_KEY":"dummy-imported-key"}`), 0600)
	s := setupTest(t, a)
	status, data, _ := setupRequest(t, s, "import", map[string]any{}, nil)
	if status != 200 || bytes.Contains(data, []byte("dummy-imported-key")) {
		t.Fatal("import failed or leaked key")
	}
	if _, err := os.Stat(a.ConfigPath()); !os.IsNotExist(err) {
		t.Fatal("import saved without confirmation")
	}
	payload := setupValues()
	payload["api_key"] = ""
	payload["base_url"] = "https://imported.invalid/v1"
	status, data, _ = setupRequest(t, s, "save", payload, nil)
	if status != 200 {
		t.Fatalf("save %s", data)
	}
	cfg, _, _ := a.Load(true)
	if cfg.APIKey != "dummy-imported-key" || cfg.Model != "image-model" {
		t.Fatal("import settings wrong")
	}
}

func TestSetupOverwriteConfirmationAndKeyRetention(t *testing.T) {
	a, _ := testApp(t)
	writeConfig(t, a, "https://images.invalid")
	s := setupTest(t, a)
	payload := setupValues()
	payload["api_key"] = ""
	status, _, _ := setupRequest(t, s, "save", payload, nil)
	if status != 409 {
		t.Fatal("overwrite not confirmed")
	}
	payload["overwrite"] = true
	status, data, _ := setupRequest(t, s, "save", payload, nil)
	if status != 200 {
		t.Fatalf("save %s", data)
	}
	cfg, _, _ := a.Load(true)
	if cfg.APIKey != "dummy-private-key" {
		t.Fatal("existing key lost")
	}
}

func TestSetupDoesNotReuseKeyForChangedAddress(t *testing.T) {
	a, _ := testApp(t)
	writeConfig(t, a, "https://images.invalid")
	s := setupTest(t, a)
	payload := setupValues()
	payload["api_key"] = ""
	payload["base_url"] = "https://other.invalid/v1"
	payload["overwrite"] = true
	status, _, _ := setupRequest(t, s, "save", payload, nil)
	if status != 400 {
		t.Fatal("key reused for other service")
	}
	cfg, _, _ := a.Load(true)
	if cfg.BaseURL != "https://images.invalid/v1" {
		t.Fatal("config changed")
	}
}

func TestSetupRejectsForeignRequests(t *testing.T) {
	for name, headers := range map[string]map[string]string{"origin": {"Origin": "https://foreign.invalid"}, "host": {"Host": "foreign.invalid"}, "token": {"X-Setup-Token": "wrong"}, "content-type": {"Content-Type": "text/plain"}} {
		t.Run(name, func(t *testing.T) {
			a, _ := testApp(t)
			s := setupTest(t, a)
			status, _, _ := setupRequest(t, s, "save", setupValues(), headers)
			expected := 403
			if name == "content-type" {
				expected = 415
			}
			if status != expected {
				t.Fatal(status)
			}
			if _, err := os.Stat(a.ConfigPath()); !os.IsNotExist(err) {
				t.Fatal("foreign request wrote configuration")
			}
		})
	}
}

func TestSetupDetectsStaleConfiguration(t *testing.T) {
	a, _ := testApp(t)
	cfg := writeConfig(t, a, "https://images.invalid")
	s := setupTest(t, a)
	cfg.Model = "changed-elsewhere"
	if err := a.Save(cfg, true); err != nil {
		t.Fatal(err)
	}
	payload := setupValues()
	payload["overwrite"] = true
	status, _, _ := setupRequest(t, s, "save", payload, nil)
	if status != 409 {
		t.Fatal("stale page overwritten config")
	}
}

func TestSetupClearingOutputRestoresDefault(t *testing.T) {
	a, _ := testApp(t)
	cfg := writeConfig(t, a, "https://images.invalid")
	cfg.OutputDir = filepath.Join(a.Home, "custom")
	_ = a.Save(cfg, true)
	s := setupTest(t, a)
	payload := setupValues()
	payload["overwrite"] = true
	payload["output_dir"] = ""
	status, _, _ := setupRequest(t, s, "save", payload, nil)
	if status != 200 {
		t.Fatal(status)
	}
	cfg, _, _ = a.Load(true)
	if cfg.OutputDir != a.DefaultOutput() {
		t.Fatal("default not restored")
	}
}

func TestSetupCancelAndExpiryDoNotWrite(t *testing.T) {
	for _, cancel := range []bool{true, false} {
		t.Run(map[bool]string{true: "cancel", false: "expiry"}[cancel], func(t *testing.T) {
			a, _ := testApp(t)
			lifetime := time.Minute
			if !cancel {
				lifetime = 30 * time.Millisecond
			}
			s, err := a.newSetup(lifetime)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			if cancel {
				status, _, _ := setupRequest(t, s, "close", map[string]any{}, nil)
				if status != 200 {
					t.Fatal(status)
				}
			}
			select {
			case <-s.done:
			case <-time.After(2 * time.Second):
				t.Fatal("session did not close")
			}
			if _, err := os.Stat(a.ConfigPath()); !os.IsNotExist(err) {
				t.Fatal("cancel wrote configuration")
			}
		})
	}
}

func TestSetupUnknownLinkIsNotAccessible(t *testing.T) {
	a, _ := testApp(t)
	s := setupTest(t, a)
	response, err := http.Get(s.origin + "/setup/wrong-token/state")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 404 {
		t.Fatal(response.StatusCode)
	}
}
