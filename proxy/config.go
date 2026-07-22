package main

import (
	"os"
	"strconv"
)

// Config holds all proxy configuration.
type Config struct {
	ListenAddr     string
	BackendAddr    string
	JWTSecret      string
	RateLimit      int // requests per second per IP
	MaxConnections int
	ReadTimeout    int // seconds
	WriteTimeout   int // seconds
	EnableMetrics  bool
	EnableAuth     bool
}

// LoadConfig creates a Config from file path, flags, and environment.
func LoadConfig(file, listen, backend string) *Config {
	cfg := &Config{
		ListenAddr:     listen,
		BackendAddr:    backend,
		JWTSecret:      getEnv("MOA_JWT_SECRET", ""),
		RateLimit:      getEnvInt("PROXY_RATE_LIMIT", 1000),
		MaxConnections: getEnvInt("PROXY_MAX_CONN", 500),
		ReadTimeout:    getEnvInt("PROXY_READ_TIMEOUT", 30),
		WriteTimeout:   getEnvInt("PROXY_WRITE_TIMEOUT", 120),
		EnableMetrics:  getEnv("PROXY_METRICS", "true") == "true",
		EnableAuth:     getEnv("PROXY_AUTH", "true") == "true",
	}
	return cfg
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getEnvInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
