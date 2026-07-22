package main

import (
	"net/http"
	"strings"
	"sync"
	"time"
)

// TokenBucket implements a per-key token bucket rate limiter.
type TokenBucket struct {
	tokens     float64
	maxTokens  float64
	refillRate float64
	lastRefill time.Time
	mu         sync.Mutex
}

// RateLimiter manages per-IP token buckets.
type RateLimiter struct {
	buckets map[string]*TokenBucket
	limit   int
	mu      sync.RWMutex
}

// NewRateLimiter creates a limiter with given RPS per key.
func NewRateLimiter(rps int) *RateLimiter {
	rl := &RateLimiter{
		buckets: make(map[string]*TokenBucket),
		limit:   rps,
	}
	// Background cleanup of stale buckets
	go rl.cleanup()
	return rl
}

// Allow checks if the key has available tokens.
func (rl *RateLimiter) Allow(key string) bool {
	rl.mu.Lock()
	bucket, exists := rl.buckets[key]
	if !exists {
		bucket = &TokenBucket{
			tokens:     float64(rl.limit),
			maxTokens:  float64(rl.limit),
			refillRate: float64(rl.limit),
			lastRefill: time.Now(),
		}
		rl.buckets[key] = bucket
	}
	rl.mu.Unlock()

	bucket.mu.Lock()
	defer bucket.mu.Unlock()

	now := time.Now()
	elapsed := now.Sub(bucket.lastRefill).Seconds()
	bucket.tokens += elapsed * bucket.refillRate
	if bucket.tokens > bucket.maxTokens {
		bucket.tokens = bucket.maxTokens
	}
	bucket.lastRefill = now

	if bucket.tokens >= 1 {
		bucket.tokens--
		return true
	}
	return false
}

// cleanup removes stale buckets every 5 minutes.
func (rl *RateLimiter) cleanup() {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for range ticker.C {
		rl.mu.Lock()
		now := time.Now()
		for key, bucket := range rl.buckets {
			bucket.mu.Lock()
			if now.Sub(bucket.lastRefill) > 10*time.Minute {
				delete(rl.buckets, key)
			}
			bucket.mu.Unlock()
		}
		rl.mu.Unlock()
	}
}

// RateLimitMiddleware returns an HTTP middleware that applies per-IP rate limiting.
func RateLimitMiddleware(cfg *Config) func(http.Handler) http.Handler {
	limiter := NewRateLimiter(cfg.RateLimit)
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := extractClientIP(r)
			if !limiter.Allow(ip) {
				w.Header().Set("Retry-After", "1")
				w.Header().Set("Content-Type", "application/json")
				http.Error(w, `{"error":"rate_limited","message":"too many requests"}`, http.StatusTooManyRequests)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func extractClientIP(r *http.Request) string {
	if forwarded := r.Header.Get("X-Forwarded-For"); forwarded != "" {
		return strings.Split(forwarded, ",")[0]
	}
	if real := r.Header.Get("X-Real-IP"); real != "" {
		return real
	}
	return r.RemoteAddr
}
