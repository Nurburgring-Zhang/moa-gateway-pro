package main

import (
	"log"
	"net"
	"net/http"
	"time"
)

// Middleware is an HTTP handler wrapper.
type Middleware func(http.Handler) http.Handler

// Chain applies middlewares in order (last applied first executed).
func Chain(handler http.Handler, middlewares ...Middleware) http.Handler {
	for i := len(middlewares) - 1; i >= 0; i-- {
		handler = middlewares[i](handler)
	}
	return handler
}

// extractClientIP returns the trusted client IP for edge use: the direct
// connection peer. Client-supplied X-Forwarded-For / X-Real-IP are ignored
// on purpose — at the proxy edge those headers are attacker-controlled,
// and forwarding them would let any client spoof its IP toward the
// backend (the Python side trusts XFF for its login rate limiter).
// (v3.2.1: this free function was referenced but never defined — the
// proxy did not compile. RateLimiter keeps its config-driven variant.)
func extractClientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil || host == "" {
		return r.RemoteAddr
	}
	return host
}

// LoggingMiddleware logs request method, path, IP and duration.
func LoggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("[%s] %s %s %v", r.Method, r.URL.Path, extractClientIP(r), time.Since(start))
	})
}

// RecoveryMiddleware catches panics and returns 500.
func RecoveryMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if err := recover(); err != nil {
				log.Printf("PANIC recovered: %v", err)
				w.Header().Set("Content-Type", "application/json")
				http.Error(w, `{"error":"internal_error"}`, http.StatusInternalServerError)
			}
		}()
		next.ServeHTTP(w, r)
	})
}
