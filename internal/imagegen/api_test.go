package imagegen

import (
	"bytes"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"image"
	"image/png"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
)

func testPNG(width, height int) []byte {
	var buffer bytes.Buffer
	_ = png.Encode(&buffer, image.NewRGBA(image.Rect(0, 0, width, height)))
	return buffer.Bytes()
}
func pngResult(w http.ResponseWriter, data []byte) {
	encode(w, map[string]any{"data": []map[string]any{{"b64_json": base64.StdEncoding.EncodeToString(data), "revised_prompt": "a cycling pelican"}}})
}
func generateOptions(a *App) Options {
	return Options{Command: "generate", Prompt: "a pelican", Count: 1, OutputDir: filepath.Join(a.Home, "output")}
}

func TestGenerationContractAndNativeOutput(t *testing.T) {
	a, _ := testApp(t)
	image := testPNG(2, 2)
	count := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		count++
		if r.Method != "POST" || r.URL.Path != "/v1/images/generations" || r.Header.Get("Authorization") != "Bearer dummy-private-key" {
			t.Error("request contract")
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["prompt"] != "a pelican" || body["model"] != DefaultModel || body["n"] != float64(1) || len(body) != 4 {
			t.Errorf("body %#v", body)
		}
		pngResult(w, image)
	}))
	defer server.Close()
	writeConfig(t, a, server.URL)
	result, err := a.Generate(generateOptions(a))
	if err != nil {
		t.Fatal(err)
	}
	object := result.(map[string]any)
	if object["status"] != "success" || count != 1 {
		t.Fatal(object)
	}
	images := object["images"].([]map[string]any)
	path := images[0]["path"].(string)
	data, _ := os.ReadFile(path)
	if !filepath.IsAbs(path) || !bytes.Equal(data, image) {
		t.Fatal("image not saved")
	}
	if strings.Contains(a.Err.(*bytes.Buffer).String(), "dummy-private-key") {
		t.Fatal("key logged")
	}
}

func TestDownloadDoesNotSendCredentials(t *testing.T) {
	a, _ := testApp(t)
	data := testPNG(2, 2)
	requests := 0
	var base string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Method == "GET" {
			if r.Header.Get("Authorization") != "" {
				t.Error("download key leaked")
			}
			_, _ = w.Write(data)
		} else {
			encode(w, map[string]any{"data": []map[string]string{{"url": base + "/image.png"}}})
		}
	}))
	defer server.Close()
	base = server.URL
	writeConfig(t, a, base)
	if _, err := a.Generate(generateOptions(a)); err != nil {
		t.Fatal(err)
	}
	if requests != 2 {
		t.Fatal(requests)
	}
}

func TestMultipartReferencesAndMask(t *testing.T) {
	a, _ := testApp(t)
	data := testPNG(2, 2)
	reference := filepath.Join(a.Home, "reference.png")
	mask := filepath.Join(a.Home, "mask.png")
	_ = os.WriteFile(reference, data, 0600)
	_ = os.WriteFile(mask, data, 0600)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/images/edits" {
			t.Error("wrong route")
		}
		if err := r.ParseMultipartForm(maxUpload); err != nil {
			t.Error(err)
		}
		defer r.MultipartForm.RemoveAll()
		if r.FormValue("prompt") != "a pelican" || len(r.MultipartForm.File["image"]) != 1 || len(r.MultipartForm.File["mask"]) != 1 {
			t.Error("multipart contract")
		}
		if r.MultipartForm.File["image"][0].Header.Get("Content-Type") != "image/png" {
			t.Error("wrong MIME")
		}
		pngResult(w, data)
	}))
	defer server.Close()
	writeConfig(t, a, server.URL)
	options := generateOptions(a)
	options.Command = "edit"
	options.References = []string{reference}
	options.Mask = mask
	if _, err := a.Generate(options); err != nil {
		t.Fatal(err)
	}
}

func TestMultipleReferencesUseImageArray(t *testing.T) {
	a, _ := testApp(t)
	data := testPNG(2, 2)
	ref := filepath.Join(a.Home, "ref.png")
	_ = os.WriteFile(ref, data, 0600)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseMultipartForm(maxUpload)
		defer r.MultipartForm.RemoveAll()
		if len(r.MultipartForm.File["image[]"]) != 2 {
			t.Error("references missing")
		}
		pngResult(w, data)
	}))
	defer server.Close()
	writeConfig(t, a, server.URL)
	options := generateOptions(a)
	options.Command = "edit"
	options.References = []string{ref, ref}
	if _, err := a.Generate(options); err != nil {
		t.Fatal(err)
	}
}

func TestInvalidInputsFailBeforeBillableRequest(t *testing.T) {
	for _, name := range []string{"mask", "fake-image", "size", "count", "empty-prompt", "format"} {
		t.Run(name, func(t *testing.T) {
			a, _ := testApp(t)
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; pngResult(w, testPNG(2, 2)) }))
			defer server.Close()
			writeConfig(t, a, server.URL)
			options := generateOptions(a)
			ref, mask := filepath.Join(a.Home, "ref.png"), filepath.Join(a.Home, "mask.png")
			_ = os.WriteFile(ref, testPNG(2, 2), 0600)
			_ = os.WriteFile(mask, testPNG(3, 3), 0600)
			switch name {
			case "mask":
				options.Command = "edit"
				options.References = []string{ref}
				options.Mask = mask
			case "fake-image":
				_ = os.WriteFile(ref, []byte(`{"key":"must-not-upload"}`), 0600)
				options.Command = "edit"
				options.References = []string{ref}
			case "size":
				options.Size = "4096x4096"
			case "count":
				options.Count = 9
			case "empty-prompt":
				options.Prompt = ""
			case "format":
				options.Background = "transparent"
				options.Format = "jpeg"
			}
			if _, err := a.Generate(options); err == nil {
				t.Fatal("invalid request accepted")
			}
			if calls != 0 {
				t.Fatal("billable request made")
			}
		})
	}
}

func TestAPIErrorDoesNotRetryOrDiscloseKey(t *testing.T) {
	a, _ := testApp(t)
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(429)
		encode(w, map[string]any{"error": map[string]string{"message": "rejected dummy-private-key"}})
	}))
	defer server.Close()
	writeConfig(t, a, server.URL)
	_, err := a.Generate(generateOptions(a))
	requireFailure(t, err, "api_error")
	if calls != 1 || strings.Contains(err.Error(), "dummy-private-key") {
		t.Fatal("retry or key leak")
	}
}

func TestAuthenticatedRedirectIsBlocked(t *testing.T) {
	a, _ := testApp(t)
	var followed atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { followed.Add(1) }))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 307) }))
	defer server.Close()
	writeConfig(t, a, server.URL)
	_, err := a.Generate(generateOptions(a))
	requireFailure(t, err, "redirect_blocked")
	if followed.Load() != 0 {
		t.Fatal("redirect followed")
	}
}

func TestPartialBatchPreservesSavedImage(t *testing.T) {
	a, _ := testApp(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		encode(w, map[string]any{"data": []map[string]string{{"b64_json": base64.StdEncoding.EncodeToString(testPNG(2, 2))}, {"b64_json": "invalid%base64"}}})
	}))
	defer server.Close()
	writeConfig(t, a, server.URL)
	options := generateOptions(a)
	options.Count = 2
	result, err := a.Generate(options)
	if err != nil {
		t.Fatal(err)
	}
	object := result.(map[string]any)
	if object["status"] != "partial" || len(object["images"].([]map[string]any)) != 1 || len(object["errors"].([]map[string]any)) != 1 {
		t.Fatal(object)
	}
}

func TestMissingImagesAreNotSuccess(t *testing.T) {
	a, _ := testApp(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = io.WriteString(w, `{"data":[]}`) }))
	defer server.Close()
	writeConfig(t, a, server.URL)
	_, err := a.Generate(generateOptions(a))
	requireFailure(t, err, "invalid_response")
}

func TestDoctorOnlySendsUnauthenticatedHead(t *testing.T) {
	a, _ := testApp(t)
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != "HEAD" || r.Header.Get("Authorization") != "" || r.URL.Path != "/v1/models" {
			t.Error("unsafe doctor")
		}
		w.WriteHeader(401)
	}))
	defer server.Close()
	writeConfig(t, a, server.URL)
	result, err := a.Doctor(2)
	if err != nil {
		t.Fatal(err)
	}
	object := result.(map[string]any)
	if object["reachable"] != true || object["tls_verified"] != false || object["credentials_sent"] != false || calls != 1 {
		t.Fatal(object)
	}
}

func TestTLSVerificationAndExplicitCA(t *testing.T) {
	a, env := testApp(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(404) }))
	defer server.Close()
	writeConfig(t, a, server.URL)
	_, err := a.Doctor(2)
	requireFailure(t, err, "tls_certificate_error")
	certPath := filepath.Join(a.Home, "test-ca.pem")
	_ = os.WriteFile(certPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0600)
	env["SSL_CERT_FILE"] = certPath
	result, err := a.Doctor(2)
	if err != nil {
		t.Fatal(err)
	}
	if result.(map[string]any)["tls_verified"] != true {
		t.Fatal("TLS not verified")
	}
}

func TestLocalHTTPDoesNotRequireCA(t *testing.T) {
	a, env := testApp(t)
	env["SSL_CERT_FILE"] = filepath.Join(a.Home, "missing.pem")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(404) }))
	defer server.Close()
	writeConfig(t, a, server.URL)
	if _, err := a.Doctor(2); err != nil {
		t.Fatal(err)
	}
}

func TestHTTPDownloadRedirectLoadsHTTPSCA(t *testing.T) {
	a, env := testApp(t)
	image := testPNG(2, 2)
	secure := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(image) }))
	defer secure.Close()
	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, secure.URL, 302) }))
	defer plain.Close()
	path := filepath.Join(a.Home, "test-ca.pem")
	_ = os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: secure.Certificate().Raw}), 0600)
	env["SSL_CERT_FILE"] = path
	got, err := a.imageBytes(imageItem{URL: plain.URL}, 2)
	if err != nil || !bytes.Equal(got, image) {
		t.Fatalf("download %v", err)
	}
}

func TestNetworkErrorsRemainDistinct(t *testing.T) {
	for name, err := range map[string]error{"dns_error": &net.DNSError{Err: "missing", Name: "invalid"}, "network_permission_denied": syscall.EPERM, "tls_certificate_error": x509.UnknownAuthorityError{}, "connection_refused": syscall.ECONNREFUSED} {
		t.Run(name, func(t *testing.T) { requireFailure(t, networkError(err), name) })
	}
}
