package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// startBackend spins a real HTTP test server acting as the Python backend.
func startBackend(t *testing.T, id string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"backend":"`+id+`","path":"`+r.URL.Path+`"}`)
	}))
}

func TestProxyForwardsToBackend(t *testing.T) {
	backend := startBackend(t, "primary")
	defer backend.Close()

	cfg := &Config{
		BackendAddr:  backend.URL,
		EnableAuth:   false,
		RateLimit:    1000,
		MaxConnections: 10,
	}
	proxy := NewProxyHandler(cfg)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/health/ready", nil)
	req.RemoteAddr = "203.0.113.5:40000"
	proxy.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("proxy returned %d, want 200; body=%s", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	if !strings.Contains(body, `"backend":"primary"`) || !strings.Contains(body, `/health/ready`) {
		t.Fatalf("response not from backend: %s", body)
	}
}

func TestProxyRoundRobinAcrossBackends(t *testing.T) {
	b1 := startBackend(t, "backend-1")
	defer b1.Close()
	b2 := startBackend(t, "backend-2")
	defer b2.Close()

	cfg := &Config{
		BackendAddr: b1.URL, // fallback entry (BACKEND_URLS absent)
		BackendURLs: []string{b1.URL, b2.URL},
		EnableAuth:  false,
		RateLimit:   1000,
	}
	proxy := NewProxyHandler(cfg)

	seen := map[string]int{}
	for i := 0; i < 6; i++ {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest("GET", "/v1/models", nil)
		req.RemoteAddr = "203.0.113.5:40001"
		proxy.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("request %d failed: %d %s", i, rec.Code, rec.Body.String())
		}
		body := rec.Body.String()
		switch {
		case strings.Contains(body, "backend-1"):
			seen["backend-1"]++
		case strings.Contains(body, "backend-2"):
			seen["backend-2"]++
		default:
			t.Fatalf("unexpected backend response: %s", body)
		}
	}
	if seen["backend-1"] != 3 || seen["backend-2"] != 3 {
		t.Fatalf("round-robin not balanced: %v", seen)
	}
}

func TestProxySingleBackendWhenEnvUnset(t *testing.T) {
	// BACKEND_URLS unset (nil) → all traffic to BackendAddr (back-compat).
	b := startBackend(t, "only")
	defer b.Close()

	cfg := &Config{BackendAddr: b.URL, EnableAuth: false, RateLimit: 1000}
	proxy := NewProxyHandler(cfg)
	for i := 0; i < 3; i++ {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest("GET", "/health", nil)
		req.RemoteAddr = "203.0.113.5:40002"
		proxy.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "only") {
			t.Fatalf("back-compat forwarding broken at req %d: %d %s", i, rec.Code, rec.Body.String())
		}
	}
}

func TestProxyDropsSpoofedXFF(t *testing.T) {
	var seenXFF string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenXFF = r.Header.Get("X-Forwarded-For")
		w.WriteHeader(http.StatusOK)
	}))
	defer backend.Close()

	cfg := &Config{BackendAddr: backend.URL, EnableAuth: false, RateLimit: 1000}
	proxy := NewProxyHandler(cfg)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/v1/models", nil)
	req.RemoteAddr = "203.0.113.77:50000"
	req.Header.Set("X-Forwarded-For", "1.2.3.4") // spoofed
	proxy.ServeHTTP(rec, req)

	if strings.Contains(seenXFF, "1.2.3.4") {
		t.Fatalf("spoofed XFF reached backend: %q", seenXFF)
	}
	if !strings.HasPrefix(seenXFF, "203.0.113.77") {
		t.Fatalf("backend must see the real peer XFF, got %q", seenXFF)
	}
}

func TestProxyEnablesAuthOnProtectedPaths(t *testing.T) {
	backend := startBackend(t, "behind-auth")
	defer backend.Close()

	cfg := &Config{
		BackendAddr: backend.URL,
		EnableAuth:  true,
		JWTSecret:   "test-secret-key-minimum-32-characters-long!",
		RateLimit:   1000,
	}
	proxy := NewProxyHandler(cfg)

	// Protected path without credentials → 401 at the proxy.
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader("{}"))
	req.RemoteAddr = "203.0.113.5:40003"
	proxy.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("protected path without auth: got %d, want 401", rec.Code)
	}

	// Public path → forwarded without credentials.
	rec2 := httptest.NewRecorder()
	req2 := httptest.NewRequest("GET", "/health", nil)
	req2.RemoteAddr = "203.0.113.5:40004"
	proxy.ServeHTTP(rec2, req2)
	if rec2.Code != http.StatusOK {
		t.Fatalf("public path: got %d, want 200", rec2.Code)
	}
}
