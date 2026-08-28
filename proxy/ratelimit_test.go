package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTokenBucketLimit(t *testing.T) {
	rl := NewRateLimiter(3, nil)
	key := "10.0.0.1"
	allowed := 0
	for i := 0; i < 5; i++ {
		if rl.Allow(key) {
			allowed++
		}
	}
	if allowed != 3 {
		t.Fatalf("limiter with rps=3 allowed %d requests in one instant, want 3", allowed)
	}
}

func TestTokenBucketPerKeyIsolation(t *testing.T) {
	rl := NewRateLimiter(2, nil)
	a, b := "10.0.0.1", "10.0.0.2"
	if !rl.Allow(a) || !rl.Allow(a) {
		t.Fatal("key A should have its own budget")
	}
	if !rl.Allow(b) {
		t.Fatal("key B budget must be independent of key A")
	}
	if rl.Allow(a) {
		t.Fatal("key A budget must be exhausted")
	}
}

func TestExtractClientIPTrustedProxy(t *testing.T) {
	rl := NewRateLimiter(100, []string{"127.0.0.1/32"})

	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "127.0.0.1:5555"
	r.Header.Set("X-Forwarded-For", "203.0.113.7, 10.0.0.9")
	if got := rl.extractClientIP(r); got != "203.0.113.7" {
		t.Fatalf("trusted proxy: got %q, want first XFF entry", got)
	}

	r2 := httptest.NewRequest("GET", "/", nil)
	r2.RemoteAddr = "8.8.8.8:4444"
	r2.Header.Set("X-Forwarded-For", "203.0.113.7")
	if got := rl.extractClientIP(r2); got != "8.8.8.8" {
		t.Fatalf("untrusted peer: got %q, want RemoteAddr host (header ignored)", got)
	}
}

func TestEdgeExtractClientIPIgnoresSpoofedHeaders(t *testing.T) {
	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "203.0.113.99:1234"
	r.Header.Set("X-Forwarded-For", "1.1.1.1")
	r.Header.Set("X-Real-IP", "2.2.2.2")
	if got := extractClientIP(r); got != "203.0.113.99" {
		t.Fatalf("edge extractClientIP = %q, want direct peer (spoofed headers ignored)", got)
	}

	r2 := httptest.NewRequest("GET", "/", nil)
	r2.RemoteAddr = "no-port-format"
	if got := extractClientIP(r2); got != "no-port-format" {
		t.Fatalf("RemoteAddr without port: got %q, want raw value", got)
	}
}

func TestRateLimitMiddlewareBlocks(t *testing.T) {
	cfg := &Config{RateLimit: 2, TrustedProxies: nil}
	handler := RateLimitMiddleware(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Every request from the same RemoteAddr shares one bucket.
	blocked := 0
	for i := 0; i < 5; i++ {
		r := httptest.NewRequest("GET", "/v1/models", nil)
		r.RemoteAddr = "9.9.9.9:1111"
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code == http.StatusTooManyRequests {
			blocked++
			if got := w.Header().Get("Retry-After"); got != "1" {
				t.Fatalf("429 must carry Retry-After: 1, got %q", got)
			}
		}
	}
	if blocked != 3 {
		t.Fatalf("expected 3 of 5 requests blocked at rps=2, got %d", blocked)
	}
}
