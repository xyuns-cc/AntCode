from antcode_gateway.rate_limit import RateLimiter, TokenBucketLimiter


def test_token_bucket_denies_when_empty():
    limiter = TokenBucketLimiter(rate=0.0, capacity=1)
    first = limiter.allow("worker-1")
    second = limiter.allow("worker-1")
    assert first.allowed is True
    assert second.allowed is False


def test_rate_limiter_blocks_when_global_empty():
    limiter = RateLimiter(global_rate=0.0, global_capacity=0, per_worker_rate=1.0, per_worker_capacity=1)
    result = limiter.allow("worker-1")
    assert result.allowed is False
