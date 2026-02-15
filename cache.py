"""
GitHub API Caching Layer.

Provides intelligent caching for GitHub API responses with:
- Redis as primary cache (fast, distributed)
- DiskCache as fallback (works without Redis)
- Endpoint-aware TTL strategies
- Cache statistics and monitoring
- Manual cache invalidation support

TTL Strategy:
- Organization metadata: 24 hours (rarely changes)
- Repository lists: 7 days (can be invalidated via webhook)
- File contents (package.json, etc.): 7 days
- Branch/PR lists: 12 hours (more dynamic)
- Issue/Discussion lists: 6 hours (frequently updated)
"""
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from config import Config

# Try to import Redis
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# Try to import diskcache as fallback
try:
    import diskcache
    DISKCACHE_AVAILABLE = True
except ImportError:
    DISKCACHE_AVAILABLE = False


@dataclass
class CacheStats:
    """Statistics for cache operations."""
    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0
    errors: int = 0
    bytes_saved: int = 0  # Approximate bytes not transferred due to cache hits
    last_reset: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate as percentage."""
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return (self.hits / total) * 100

    def to_dict(self) -> Dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            'hits': self.hits,
            'misses': self.misses,
            'sets': self.sets,
            'deletes': self.deletes,
            'errors': self.errors,
            'hit_rate_percent': round(self.hit_rate, 2),
            'bytes_saved_approx': self.bytes_saved,
            'last_reset': self.last_reset,
        }


class CacheKeyBuilder:
    """Builds cache keys with endpoint-aware TTL detection."""

    # Patterns for determining TTL based on GitHub API endpoint
    ENDPOINT_PATTERNS = [
        # Organization metadata
        (r'^/orgs/[^/]+$', 'org_metadata', Config.CACHE_TTL_ORG_METADATA),
        (r'^/users/[^/]+$', 'user_metadata', Config.CACHE_TTL_ORG_METADATA),

        # Repository lists
        (r'^/orgs/[^/]+/repos', 'repo_list', Config.CACHE_TTL_REPO_LIST),
        (r'^/users/[^/]+/repos', 'repo_list', Config.CACHE_TTL_REPO_LIST),

        # File contents
        (r'^/repos/[^/]+/[^/]+/contents/', 'file_content', Config.CACHE_TTL_FILE_CONTENT),
        (r'^/repos/[^/]+/[^/]+/git/trees', 'file_content', Config.CACHE_TTL_FILE_CONTENT),

        # Branch/PR lists
        (r'^/repos/[^/]+/[^/]+/branches', 'branch_list', Config.CACHE_TTL_BRANCH_LIST),
        (r'^/repos/[^/]+/[^/]+/pulls', 'pr_list', Config.CACHE_TTL_BRANCH_LIST),

        # Issue/Discussion lists
        (r'^/repos/[^/]+/[^/]+/issues', 'issue_list', Config.CACHE_TTL_ISSUE_LIST),
        (r'^/search/', 'search', Config.CACHE_TTL_ISSUE_LIST),
    ]

    @classmethod
    def get_cache_key(cls, url: str, params: Optional[Dict] = None) -> str:
        """
        Generate a cache key from URL and parameters.

        Args:
            url: The GitHub API URL
            params: Optional query parameters

        Returns:
            A unique cache key string
        """
        # Parse the URL to get the path
        parsed = urlparse(url)
        path = parsed.path

        # Remove the API base to get relative path
        if path.startswith('/'):
            path = path[1:]

        # Create a stable key from path + sorted params
        key_parts = [path]
        if params:
            sorted_params = sorted(params.items())
            key_parts.append(json.dumps(sorted_params, sort_keys=True))

        key_string = '|'.join(key_parts)

        # Hash for consistent key length
        key_hash = hashlib.sha256(key_string.encode()).hexdigest()[:16]

        # Prefix with readable endpoint type
        endpoint_type = cls._get_endpoint_type(url)

        return f"gh:{endpoint_type}:{key_hash}"

    @classmethod
    def get_ttl_for_url(cls, url: str) -> int:
        """
        Determine the appropriate TTL for a GitHub API URL.

        Args:
            url: The GitHub API URL

        Returns:
            TTL in seconds
        """
        parsed = urlparse(url)
        path = parsed.path

        # Remove API base prefix
        api_path = path.replace('/api/v3', '').replace('https://api.github.com', '')
        if not api_path.startswith('/'):
            api_path = '/' + api_path

        for pattern, _, ttl in cls.ENDPOINT_PATTERNS:
            if re.match(pattern, api_path):
                return ttl

        return Config.CACHE_TTL_DEFAULT

    @classmethod
    def _get_endpoint_type(cls, url: str) -> str:
        """Get a human-readable endpoint type for the cache key prefix."""
        parsed = urlparse(url)
        path = parsed.path

        api_path = path.replace('/api/v3', '').replace('https://api.github.com', '')
        if not api_path.startswith('/'):
            api_path = '/' + api_path

        for pattern, endpoint_type, _ in cls.ENDPOINT_PATTERNS:
            if re.match(pattern, api_path):
                return endpoint_type

        return 'other'


class GitHubCache:
    """
    Intelligent caching layer for GitHub API responses.

    Features:
    - Redis as primary cache (if available)
    - DiskCache as automatic fallback
    - Endpoint-aware TTL strategies
    - Thread-safe statistics tracking
    - Manual invalidation support
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stats = CacheStats()
        self._redis_client: Optional['redis.Redis'] = None
        self._disk_cache: Optional['diskcache.Cache'] = None
        self._backend: str = 'none'

        self._initialize_backend()

    def _initialize_backend(self):
        """Initialize the cache backend (Redis or DiskCache fallback)."""
        if not Config.CACHE_ENABLED:
            self._backend = 'disabled'
            print("[CACHE] Caching is disabled via CACHE_ENABLED=false")
            return

        # Try Redis first
        if REDIS_AVAILABLE:
            try:
                if Config.REDIS_URL:
                    self._redis_client = redis.from_url(
                        Config.REDIS_URL,
                        decode_responses=True,
                        socket_connect_timeout=5
                    )
                else:
                    self._redis_client = redis.Redis(
                        host=Config.REDIS_HOST,
                        port=Config.REDIS_PORT,
                        db=Config.REDIS_DB,
                        password=Config.REDIS_PASSWORD,
                        decode_responses=True,
                        socket_connect_timeout=5
                    )

                # Test connection
                self._redis_client.ping()
                self._backend = 'redis'
                print(f"[CACHE] Redis connected: {Config.REDIS_HOST}:{Config.REDIS_PORT}")
                return

            except (redis.ConnectionError, redis.TimeoutError) as e:
                print(f"[CACHE] Redis connection failed: {e}")
                self._redis_client = None

        # Fall back to DiskCache
        if DISKCACHE_AVAILABLE:
            try:
                os.makedirs(Config.CACHE_FALLBACK_DIR, exist_ok=True)
                self._disk_cache = diskcache.Cache(
                    Config.CACHE_FALLBACK_DIR,
                    size_limit=500 * 1024 * 1024  # 500 MB limit
                )
                self._backend = 'diskcache'
                print(f"[CACHE] DiskCache initialized: {Config.CACHE_FALLBACK_DIR}")
                return

            except Exception as e:
                print(f"[CACHE] DiskCache initialization failed: {e}")
                self._disk_cache = None

        self._backend = 'none'
        print("[CACHE] No cache backend available. Running without cache.")

    def get(self, url: str, params: Optional[Dict] = None) -> Optional[Tuple[int, Dict, str]]:
        """
        Get a cached response for a GitHub API request.

        Args:
            url: The GitHub API URL
            params: Optional query parameters

        Returns:
            Tuple of (status_code, headers_dict, body_json) if cached, None otherwise
        """
        if self._backend in ('none', 'disabled'):
            return None

        key = CacheKeyBuilder.get_cache_key(url, params)

        try:
            cached_data = None

            if self._backend == 'redis' and self._redis_client:
                cached_json = self._redis_client.get(key)
                if cached_json:
                    cached_data = json.loads(cached_json)

            elif self._backend == 'diskcache' and self._disk_cache:
                cached_data = self._disk_cache.get(key)

            if cached_data:
                with self._lock:
                    self._stats.hits += 1
                    # Estimate bytes saved (rough approximation)
                    self._stats.bytes_saved += len(json.dumps(cached_data.get('body', {})))

                return (
                    cached_data.get('status_code', 200),
                    cached_data.get('headers', {}),
                    cached_data.get('body', {})
                )

            with self._lock:
                self._stats.misses += 1

            return None

        except Exception as e:
            with self._lock:
                self._stats.errors += 1
            print(f"[CACHE] Get error for {key}: {e}")
            return None

    def set(self, url: str, params: Optional[Dict], status_code: int,
            headers: Dict, body: Any, ttl: Optional[int] = None):
        """
        Cache a GitHub API response.

        Args:
            url: The GitHub API URL
            params: Query parameters used
            status_code: HTTP status code
            headers: Response headers
            body: Response body (JSON-serializable)
            ttl: Optional TTL override in seconds
        """
        if self._backend in ('none', 'disabled'):
            return

        # Only cache successful responses
        if status_code not in (200, 304):
            return

        key = CacheKeyBuilder.get_cache_key(url, params)
        actual_ttl = ttl if ttl is not None else CacheKeyBuilder.get_ttl_for_url(url)

        cache_data = {
            'status_code': status_code,
            'headers': dict(headers) if headers else {},
            'body': body,
            'cached_at': datetime.now().isoformat(),
            'ttl': actual_ttl,
        }

        try:
            if self._backend == 'redis' and self._redis_client:
                self._redis_client.setex(
                    key,
                    actual_ttl,
                    json.dumps(cache_data)
                )

            elif self._backend == 'diskcache' and self._disk_cache:
                self._disk_cache.set(key, cache_data, expire=actual_ttl)

            with self._lock:
                self._stats.sets += 1

        except Exception as e:
            with self._lock:
                self._stats.errors += 1
            print(f"[CACHE] Set error for {key}: {e}")

    def invalidate(self, pattern: str) -> int:
        """
        Invalidate cache entries matching a pattern.

        Args:
            pattern: Pattern to match (e.g., "gh:repo_list:*" or org name)

        Returns:
            Number of keys invalidated
        """
        if self._backend in ('none', 'disabled'):
            return 0

        deleted = 0

        try:
            if self._backend == 'redis' and self._redis_client:
                # Redis supports pattern-based deletion
                if '*' in pattern:
                    keys = self._redis_client.keys(pattern)
                else:
                    # Treat as org name - invalidate all related keys
                    keys = self._redis_client.keys(f"gh:*:{pattern}*")

                if keys:
                    deleted = self._redis_client.delete(*keys)

            elif self._backend == 'diskcache' and self._disk_cache:
                # DiskCache doesn't support patterns well, so we iterate
                # This is slower but works for the fallback case
                keys_to_delete = []
                for key in self._disk_cache.iterkeys():
                    if pattern in key or (pattern.replace('*', '') in key):
                        keys_to_delete.append(key)

                for key in keys_to_delete:
                    self._disk_cache.delete(key)
                    deleted += 1

            with self._lock:
                self._stats.deletes += deleted

        except Exception as e:
            with self._lock:
                self._stats.errors += 1
            print(f"[CACHE] Invalidate error for pattern {pattern}: {e}")

        return deleted

    def invalidate_org(self, org_login: str) -> int:
        """
        Invalidate all cache entries for a specific organization.

        Useful when receiving a webhook that an org's repo was pushed.

        Args:
            org_login: The GitHub organization login

        Returns:
            Number of keys invalidated
        """
        # Build patterns for org-related endpoints
        patterns = [
            f"gh:org_metadata:*{org_login}*",
            f"gh:repo_list:*{org_login}*",
            f"gh:branch_list:*{org_login}*",
            f"gh:pr_list:*{org_login}*",
            f"gh:issue_list:*{org_login}*",
            f"gh:file_content:*{org_login}*",
        ]

        total_deleted = 0
        for pattern in patterns:
            total_deleted += self.invalidate(pattern)

        print(f"[CACHE] Invalidated {total_deleted} keys for org: {org_login}")
        return total_deleted

    def clear_all(self) -> int:
        """
        Clear all cached entries.

        Returns:
            Number of keys cleared
        """
        if self._backend in ('none', 'disabled'):
            return 0

        deleted = 0

        try:
            if self._backend == 'redis' and self._redis_client:
                keys = self._redis_client.keys("gh:*")
                if keys:
                    deleted = self._redis_client.delete(*keys)

            elif self._backend == 'diskcache' and self._disk_cache:
                deleted = len(self._disk_cache)
                self._disk_cache.clear()

            with self._lock:
                self._stats.deletes += deleted

        except Exception as e:
            with self._lock:
                self._stats.errors += 1
            print(f"[CACHE] Clear all error: {e}")

        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats and backend info
        """
        with self._lock:
            stats = self._stats.to_dict()

        stats['backend'] = self._backend
        stats['enabled'] = Config.CACHE_ENABLED

        # Add backend-specific info
        if self._backend == 'redis' and self._redis_client:
            try:
                info = self._redis_client.info('memory')
                stats['redis_memory_used'] = info.get('used_memory_human', 'unknown')
                stats['redis_keys'] = self._redis_client.dbsize()
            except Exception:
                pass

        elif self._backend == 'diskcache' and self._disk_cache:
            try:
                stats['diskcache_size'] = self._disk_cache.volume()
                stats['diskcache_count'] = len(self._disk_cache)
            except Exception:
                pass

        # Add TTL configuration
        stats['ttl_config'] = {
            'org_metadata': Config.CACHE_TTL_ORG_METADATA,
            'repo_list': Config.CACHE_TTL_REPO_LIST,
            'file_content': Config.CACHE_TTL_FILE_CONTENT,
            'branch_list': Config.CACHE_TTL_BRANCH_LIST,
            'issue_list': Config.CACHE_TTL_ISSUE_LIST,
            'default': Config.CACHE_TTL_DEFAULT,
        }

        return stats

    def reset_stats(self):
        """Reset cache statistics."""
        with self._lock:
            self._stats = CacheStats()

    def is_available(self) -> bool:
        """Check if caching is available and enabled."""
        return self._backend not in ('none', 'disabled')

    def get_backend_name(self) -> str:
        """Get the name of the active cache backend."""
        return self._backend


# Global cache instance
_github_cache: Optional[GitHubCache] = None
_cache_init_lock = threading.Lock()


def get_github_cache() -> GitHubCache:
    """Get the global GitHub cache instance (lazy initialization)."""
    global _github_cache

    if _github_cache is None:
        with _cache_init_lock:
            if _github_cache is None:
                _github_cache = GitHubCache()

    return _github_cache


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics (convenience function)."""
    return get_github_cache().get_stats()


def invalidate_org_cache(org_login: str) -> int:
    """Invalidate cache for an organization (convenience function)."""
    return get_github_cache().invalidate_org(org_login)


def clear_cache() -> int:
    """Clear all cache entries (convenience function)."""
    return get_github_cache().clear_all()
