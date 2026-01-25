# Rate Limiting & Caching Guide

## HTTP 429 (Too Many Requests) Solutions

The application now includes several features to handle rate limiting:

### 1. **Response Caching**
- API responses are cached in the database
- Default cache TTL: 1 hour (configurable via `API_CACHE_TTL`)
- Reduces API calls by serving cached data when available
- Cache is automatically checked before making API requests

### 2. **Request Throttling**
- Minimum interval between requests (default: 100ms)
- Configurable via `API_MIN_REQUEST_INTERVAL`
- Prevents rapid-fire requests that trigger rate limits

### 3. **Automatic Retry with Exponential Backoff**
- On 429 errors, the client automatically retries up to 3 times
- Wait times: 1s, 2s, 4s (exponential backoff)
- Logs warnings but continues operation

### 4. **Configuration Options**

Add to your `.env` file:

```bash
# Cache API responses for 2 hours (7200 seconds)
API_CACHE_TTL=7200

# Wait 200ms between API requests
API_MIN_REQUEST_INTERVAL=0.2

# Update data every 2 hours instead of 1 hour
UPDATE_INTERVAL=7200
```

### 5. **Manual Cache Management**

Clear the cache via API:
```bash
curl -X POST http://localhost:8000/api/cache/clear
```

Or programmatically:
```python
from backend.api_cache import APICache
from backend.database import Database

db = Database()
cache = APICache(db)
cache.clear()  # Clear all cached responses
```

### 6. **Best Practices**

**For Free Tier (10 requests/minute):**
- Set `API_CACHE_TTL=3600` (1 hour)
- Set `UPDATE_INTERVAL=3600` (1 hour)
- This limits to ~10 requests per hour

**For Paid Tier:**
- Adjust `API_CACHE_TTL` based on how fresh you need data
- Reduce `UPDATE_INTERVAL` if you need more frequent updates
- Monitor logs for 429 errors

### 7. **Monitoring**

Check logs for:
- `Cache hit for: ...` - Good! Using cached data
- `Rate limited (429) for ...` - Retrying automatically
- `429 Too Many Requests: Rate limit exceeded` - Need to increase intervals

### 8. **Alternative Solutions**

If you continue to hit rate limits:

1. **Increase cache TTL** - Store data longer
2. **Increase update interval** - Update less frequently
3. **Use multiple API keys** - Rotate between keys (requires code changes)
4. **Consider paid tier** - Higher rate limits
5. **Alternative APIs** - Consider other football data APIs (requires integration)

### 9. **Cache Statistics**

The cache is stored in the `api_cache` table. You can query it:

```sql
SELECT COUNT(*) as cached_endpoints, 
       MIN(cached_at) as oldest_cache,
       MAX(cached_at) as newest_cache
FROM api_cache;
```

