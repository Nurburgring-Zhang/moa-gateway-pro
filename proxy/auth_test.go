package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// makeToken builds a real HS256 JWT for testing verifyJWT.
func makeToken(t *testing.T, secret string, header map[string]interface{}, claims map[string]interface{}) string {
	t.Helper()
	mustJSON := func(v interface{}) []byte {
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		return b
	}
	enc := base64.RawURLEncoding
	h := enc.EncodeToString(mustJSON(header))
	p := enc.EncodeToString(mustJSON(claims))
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(h + "." + p))
	return h + "." + p + "." + enc.EncodeToString(mac.Sum(nil))
}

func newTestHandler(secret string, enableAuth bool) *ProxyHandler {
	cfg := &Config{
		ListenAddr:  ":0",
		BackendAddr: "http://127.0.0.1:1", // never dialed in auth tests
		JWTSecret:   secret,
		EnableAuth:  enableAuth,
		RateLimit:   1000,
	}
	return NewProxyHandler(cfg)
}

func stdClaims() map[string]interface{} {
	return map[string]interface{}{
		"sub": "admin",
		"iss": "moa-gateway",
		"aud": "moa-webui",
		"exp": float64(time.Now().Add(time.Hour).Unix()),
		"iat": float64(time.Now().Unix()),
	}
}

func TestVerifyJWT(t *testing.T) {
	secret := "test-secret-key-minimum-32-characters-long!"
	h := newTestHandler(secret, true)

	cases := []struct {
		name   string
		header map[string]interface{}
		claims func() map[string]interface{}
		want   bool
	}{
		{"valid", map[string]interface{}{"alg": "HS256", "typ": "JWT"}, stdClaims, true},
		{"expired", map[string]interface{}{"alg": "HS256"}, func() map[string]interface{} {
			c := stdClaims()
			c["exp"] = float64(time.Now().Add(-time.Hour).Unix())
			return c
		}, false},
		{"alg_none", map[string]interface{}{"alg": "none"}, stdClaims, false},
		{"alg_RS256", map[string]interface{}{"alg": "RS256"}, stdClaims, false},
		{"missing_alg", map[string]interface{}{}, stdClaims, false},
		{"missing_exp", map[string]interface{}{"alg": "HS256"}, func() map[string]interface{} {
			c := stdClaims()
			delete(c, "exp")
			return c
		}, false},
		{"wrong_aud", map[string]interface{}{"alg": "HS256"}, func() map[string]interface{} {
			c := stdClaims()
			c["aud"] = "other-client"
			return c
		}, false},
		{"wrong_iss", map[string]interface{}{"alg": "HS256"}, func() map[string]interface{} {
			c := stdClaims()
			c["iss"] = "other-issuer"
			return c
		}, false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			tok := makeToken(t, secret, tc.header, tc.claims())
			if got := h.verifyJWT(tok); got != tc.want {
				t.Fatalf("verifyJWT(%s) = %v, want %v", tc.name, got, tc.want)
			}
		})
	}
}

func TestVerifyJWTTamperedPayload(t *testing.T) {
	secret := "test-secret-key-minimum-32-characters-long!"
	h := newTestHandler(secret, true)
	tok := makeToken(t, secret, map[string]interface{}{"alg": "HS256"}, stdClaims())

	// flip the payload role claim and re-encode WITHOUT re-signing
	parts := strings.Split(tok, ".")
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		t.Fatal(err)
	}
	var claims map[string]interface{}
	if err := json.Unmarshal(payload, &claims); err != nil {
		t.Fatal(err)
	}
	claims["role"] = "admin"
	forged, _ := json.Marshal(claims)
	tampered := parts[0] + "." + base64.RawURLEncoding.EncodeToString(forged) + "." + parts[2]
	if h.verifyJWT(tampered) {
		t.Fatal("tampered payload must fail signature verification")
	}

	// wrong-secret token must fail too
	other := makeToken(t, "attacker-secret-32-characters-long!!!!!!", map[string]interface{}{"alg": "HS256"}, stdClaims())
	if h.verifyJWT(other) {
		t.Fatal("token signed with wrong secret must be rejected")
	}
}

func TestVerifyJWTMalformed(t *testing.T) {
	h := newTestHandler("test-secret-key-minimum-32-characters-long!", true)
	for _, tok := range []string{"", "a.b", "a.b.c.d", "!!!.???.###"} {
		if h.verifyJWT(tok) {
			t.Fatalf("malformed token %q must be rejected", tok)
		}
	}
}

func TestQuickAuthCheck(t *testing.T) {
	secret := "test-secret-key-minimum-32-characters-long!"
	h := newTestHandler(secret, true)
	valid := makeToken(t, secret, map[string]interface{}{"alg": "HS256"}, stdClaims())

	mk := func(auth, apiKey string) *http.Request {
		r := httptest.NewRequest("GET", "/v1/chat/completions", nil)
		if auth != "" {
			r.Header.Set("Authorization", auth)
		}
		if apiKey != "" {
			r.Header.Set("X-API-Key", apiKey)
		}
		return r
	}

	if !h.quickAuthCheck(mk("Bearer "+valid, "")) {
		t.Fatal("valid bearer token must pass")
	}
	if h.quickAuthCheck(mk("Bearer garbage.token.here", "")) {
		t.Fatal("invalid bearer token must fail")
	}
	if h.quickAuthCheck(mk("Basic dXNlcjpwYXNz", "")) {
		t.Fatal("non-bearer Authorization must fail")
	}
	if h.quickAuthCheck(mk("", "")) {
		t.Fatal("no credentials at all must fail")
	}
	if !h.quickAuthCheck(mk("", "mgw-some-api-key")) {
		t.Fatal("X-API-Key fallback must pass")
	}

	// empty secret = pass-through mode (documented behaviour)
	h2 := newTestHandler("", true)
	if !h2.quickAuthCheck(mk("Bearer whatever", "")) {
		t.Fatal("empty secret must pass through to backend auth")
	}
}

func TestQuickAuthCheckMGWBearerPrefix(t *testing.T) {
	// v3.2.1: OpenAI clients send the gateway key as "Bearer mgw-..." —
	// the proxy must forward these to the backend, not treat them as JWTs.
	secret := "test-secret-key-minimum-32-characters-long!"
	h := newTestHandler(secret, true)

	mk := func(auth string) *http.Request {
		r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
		r.Header.Set("Authorization", auth)
		return r
	}

	if !h.quickAuthCheck(mk("Bearer mgw-smoke-key-12345")) {
		t.Fatal("gateway API key via Bearer must pass the proxy (backend validates)")
	}
	if !h.quickAuthCheck(mk("Bearer mgw-")) {
		t.Fatal("mgw- prefixed tokens forward regardless of suffix (backend owns them)")
	}
	// a JWT-shaped token NOT starting with mgw- still gets verified
	valid := makeToken(t, secret, map[string]interface{}{"alg": "HS256"}, stdClaims())
	if !h.quickAuthCheck(mk("Bearer "+valid)) {
		t.Fatal("valid JWT must still be verified and pass")
	}
	// a JWT-shaped token starting with mgw- skips verification by design
	// (backend is the authority for keys); document via a junk token passing
	if !h.quickAuthCheck(mk("Bearer mgw-not-a-jwt")) {
		t.Fatal("mgw- prefix takes precedence over JWT verification")
	}
	// non-mgw garbage Bearer values are still rejected at the edge
	if h.quickAuthCheck(mk("Bearer random-garbage")) {
		t.Fatal("non-JWT, non-mgw Bearer token must be rejected at the proxy")
	}
}

func TestRequiresAuthPublicPaths(t *testing.T) {
	public := []string{"/health", "/metrics", "/v1/models", "/openapi.json", "/docs", "/docs/oauth2-redirect"}
	for _, p := range public {
		if requiresAuth(p) {
			t.Errorf("path %q must be public", p)
		}
	}
	if !requiresAuth("/v1/chat/completions") {
		t.Fatal("chat completions must require auth")
	}
	if !requiresAuth("/api/admin/stats") {
		t.Fatal("admin API must require auth")
	}
}
