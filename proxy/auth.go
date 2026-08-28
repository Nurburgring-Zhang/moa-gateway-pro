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
		// Fallback: API Key header only (query-string removed for security —
		// keys in URLs leak via logs, Referer headers, and browser history)
		apiKey := r.Header.Get("X-API-Key")
		return apiKey != ""
	}

	if !strings.HasPrefix(auth, "Bearer ") {
		return false
	}
	token := strings.TrimPrefix(auth, "Bearer ")

	// v3.2.1 fix (found by live smoke): OpenAI-compatible clients send the
	// gateway API key as "Authorization: Bearer mgw-..." — that is NOT a
	// JWT. Gateway keys are owned by the Python backend; forward them and
	// let the backend validate (it answers 401 itself for bad keys).
	// Everything JWT-shaped (3 dot-separated segments) is verified here.
	if strings.HasPrefix(token, "mgw-") {
		return true
	}

	// If no secret configured, pass through to backend
	if h.cfg.JWTSecret == "" {
		return true
	}

	return h.verifyJWT(token)
}

// verifyJWT validates HS256 signature, algorithm, audience, issuer, and expiry.
func (h *ProxyHandler) verifyJWT(token string) bool {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return false
	}

	// Decode and validate header algorithm (prevent alg=none attack)
	headerBytes, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return false
	}
	var header map[string]interface{}
	if err := json.Unmarshal(headerBytes, &header); err != nil {
		return false
	}
	alg, _ := header["alg"].(string)
	if alg != "HS256" {
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

	// Decode payload and verify claims
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return false
	}
	var claims map[string]interface{}
	if err := json.Unmarshal(payload, &claims); err != nil {
		return false
	}

	// Verify expiry
	if exp, ok := claims["exp"].(float64); ok {
		if time.Now().Unix() > int64(exp) {
			return false
		}
	} else {
		return false
	}

	// Verify audience
	if aud, ok := claims["aud"].(string); ok {
		if aud != "moa-webui" {
			return false
		}
	}

	// Verify issuer
	if iss, ok := claims["iss"].(string); ok {
		if iss != "moa-gateway" {
			return false
		}
	}

	return true
}
