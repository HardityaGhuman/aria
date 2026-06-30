"""Shared test setup. Sets a JWT secret before any backend module imports config,
so token signing/verification works in tests without a real .env secret."""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-0123456789abcdef")
os.environ.setdefault("JWT_EXPIRY_HOURS", "8")
