package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

// quickAuthCheck performs fast JWT or API-key validation at the proxy layer.
func (h *ProxyHandler) quickAuthCheck(r *http.Request) bool {
	auth := r.Header.Get("Authorization")
	if auth == "" {
		// Fallback: API Key (validated by backend)
		apiKey := r.Header.Get("X-API-Key")
		if apiKey == "" {
			apiKey = r.URL.Query().Get("api_key")
		}
		return apiKey != ""
	}

	if !strings.HasPrefix(auth, "Bearer ") {
		return false
	}
	token := strings.TrimPrefix(auth, "Bearer ")

	// If no secret configured, pass through to backend
	if h.cfg.JWTSecret == "" {
		return true
	}

	return h.verifyJWT(token)
}

// verifyJWT validates HS256 signature and expiry only (fast path).
func (h *ProxyHandler) verifyJWT(token string) bool {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return false
	}

	// Verify HMAC-SHA256 signature
	signingInput := parts[0] + "." + parts[1]
	mac := hmac.New(sha256.New, []byte(h.cfg.JWTSecret))
	mac.Write([]byte(signingInput))
	expectedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))

	if !hmac.Equal([]byte(parts[2]), []byte(expectedSig)) {
		return false
	}

	// Verify expiry
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return false
	}
	var claims map[string]interface{}
	if err := json.Unmarshal(payload, &claims); err != nil {
		return false
	}

	if exp, ok := claims["exp"].(float64); ok {
		if time.Now().Unix() > int64(exp) {
			return false
		}
	}

	return true
}
