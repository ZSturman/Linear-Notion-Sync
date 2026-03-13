"""Retry utilities with exponential backoff and rate limit handling."""

import asyncio
import functools
import random
import time
from typing import Any, Callable, Optional, TypeVar, Union

from .logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class RateLimitError(Exception):
    """Raised when rate limit is hit and cannot be recovered."""
    
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class RetryableError(Exception):
    """Base class for errors that should trigger a retry."""
    pass


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None,
) -> Callable:
    """Decorator for retry with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        exponential_base: Base for exponential backoff calculation
        jitter: Add random jitter to delays
        retryable_exceptions: Tuple of exception types to retry on
        on_retry: Optional callback called on each retry with (exception, attempt)
        
    Returns:
        Decorated function
        
    Example:
        @with_retry(max_retries=3, base_delay=1.0)
        def api_call():
            return requests.get("https://api.example.com")
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[Exception] = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}",
                            exc_info=True,
                        )
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(
                        base_delay * (exponential_base ** attempt),
                        max_delay,
                    )
                    
                    # Add jitter
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    
                    # Check for rate limit specific handling
                    if hasattr(e, "retry_after") and e.retry_after:
                        delay = max(delay, e.retry_after)
                    
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                        f"after {delay:.2f}s: {str(e)}"
                    )
                    
                    if on_retry:
                        on_retry(e, attempt)
                    
                    time.sleep(delay)
            
            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected retry loop exit")
        
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[Exception] = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}",
                            exc_info=True,
                        )
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(
                        base_delay * (exponential_base ** attempt),
                        max_delay,
                    )
                    
                    # Add jitter
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    
                    # Check for rate limit specific handling
                    if hasattr(e, "retry_after") and e.retry_after:
                        delay = max(delay, e.retry_after)
                    
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                        f"after {delay:.2f}s: {str(e)}"
                    )
                    
                    if on_retry:
                        on_retry(e, attempt)
                    
                    await asyncio.sleep(delay)
            
            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected retry loop exit")
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


class RateLimiter:
    """Simple rate limiter for API calls.
    
    Uses a token bucket algorithm to limit requests per second.
    """
    
    def __init__(self, requests_per_second: float = 3.0):
        """Initialize rate limiter.
        
        Args:
            requests_per_second: Maximum requests allowed per second
        """
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self._last_request_time: float = 0
        self._lock = asyncio.Lock() if asyncio.get_event_loop_policy() else None
    
    def wait_sync(self) -> None:
        """Wait synchronously if necessary to respect rate limit."""
        now = time.time()
        time_since_last = now - self._last_request_time
        
        if time_since_last < self.min_interval:
            sleep_time = self.min_interval - time_since_last
            time.sleep(sleep_time)
        
        self._last_request_time = time.time()
    
    async def wait_async(self) -> None:
        """Wait asynchronously if necessary to respect rate limit."""
        # Use async lock if available
        if self._lock is None:
            self._lock = asyncio.Lock()
            
        async with self._lock:
            now = time.time()
            time_since_last = now - self._last_request_time
            
            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                await asyncio.sleep(sleep_time)
            
            self._last_request_time = time.time()
