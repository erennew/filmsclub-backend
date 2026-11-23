# CDN and Edge Caching Setup Guide for FilmsClub

This guide helps you configure CDN (Content Delivery Network) caching for optimal performance.

## Overview

FilmsClub implements a **multi-layer caching strategy**:

```
Browser Cache → CDN Edge Cache → Redis Cache → In-Memory Cache → MongoDB
     ↑              ↑              ↑               ↑               ↑
  (0ms)          (50ms)         (5ms)           (1ms)          (200ms+)
```

## CloudFlare Configuration (Recommended)

### Step 1: Sign Up and Add Domain

1. Sign up at [CloudFlare](https://dash.cloudflare.com/sign-up)
2. Add your domain
3. Follow DNS setup instructions
4. Wait for DNS propagation (usually 5-30 minutes)

### Step 2: Configure Page Rules

Go to **Rules → Page Rules** and add these rules:

#### Rule 1: Trending API (High Cache Priority)
```
URL Pattern: yourdomain.com/api/trending/*
Settings:
  ✓ Cache Level: Cache Everything
  ✓ Edge Cache TTL: 1 minute
  ✓ Browser Cache TTL: 1 minute
```

#### Rule 2: Media Info API (Long Cache)
```
URL Pattern: yourdomain.com/api/media-info/*
Settings:
  ✓ Cache Level: Cache Everything
  ✓ Edge Cache TTL: 24 hours
  ✓ Browser Cache TTL: 24 hours
```

#### Rule 3: Search API (No Cache)
```
URL Pattern: yourdomain.com/api/search/*
Settings:
  ✓ Cache Level: Bypass
  ✓ (Or use 'Standard' with short TTL if needed)
```

#### Rule 4: Static Assets (Long Cache)
```
URL Pattern: yourdomain.com/static/*
Settings:
  ✓ Cache Level: Cache Everything
  ✓ Edge Cache TTL: 1 month
  ✓ Browser Cache TTL: 1 month
```

#### Rule 5: Video Streams (Do Not Cache)
```
URL Pattern: yourdomain.com/dl/*
Settings:
  ✓ Cache Level: Bypass
  ✓ Disable cache for this rule
  ⚠️ Stream URLs are bearer credentials with a 12h TTL. Caching them at the
  edge would let a leaked token replay content and would expose media to
  shared caches. The backend also sends Cache-Control: private, no-store.
```

### Step 3: Enable Performance Features

In **Speed → Optimization**:

1. **Auto Minify**: Enable for JS, CSS, HTML
2. **Brotli**: Enable (better compression than gzip)
3. **Rocket Loader™**: Enable for JavaScript
4. **Early Hints**: Enable (faster page loads)

### Step 4: Configure Caching

In **Caching → Configuration**:

1. **Caching Level**: Standard
2. **Browser Cache Expiration**: Respect existing headers
3. **Always Online™**: On (serves cached pages if origin is down)

### Step 5: Enable Argo Smart Routing (Optional, Paid)

In **Speed → Argo**:

- Enable Argo Smart Routing for faster dynamic content delivery
- Cost: ~$5/month + $0.10/GB

## API Response Headers

The backend automatically adds these cache headers:

```http
Cache-Control: public, max-age=60
Vary: Accept-Encoding
```

### Custom Header Rules in CloudFlare

The backend already sets security headers and removes `Server`. Use Transform
Rules to add the origin lock:

```
# Inject the shared secret on every origin request
Add header: X-Edge-Key = $EDGE_SHARED_SECRET

# Optional: strip any client-supplied copy first (Cloudflare overwrites on Set)
Set header: X-Edge-Key = $EDGE_SHARED_SECRET
```

Set `EDGE_SHARED_SECRET` to the same value on Heroku. The origin then returns
404 for every request that does not arrive through Cloudflare, including direct
requests to the `*.herokuapp.com` host.

### Hiding the origin

1. Point a custom API hostname (for example `api.example.com`) at the Heroku
   host through Cloudflare with the orange proxy enabled.
2. Set `BASE_URL=https://api.example.com` on Heroku and the frontend's
   `VITE_BASE_URL=https://api.example.com`.
3. Do not link or document the raw Heroku hostname anywhere public.
4. Enable Cloudflare WAF managed rules and a rate-limit rule for:
   - `/api/*` — 180 requests/minute per IP
   - `/dl/*` — 300 requests/minute per IP
   - `/admin*` — 20 requests/minute per IP, optionally IP-allowlisted
5. Set `TRUST_CLOUDFLARE=true` on Heroku so rate limits key off
   `CF-Connecting-IP` instead of a spoofable header.
6. Block direct origin access at the network layer if the plan supports it
   (Heroku Private Spaces / firewall, or a proxy that only accepts Cloudflare
   IP ranges). The edge lock above covers the application layer.

## Cache Invalidation

When you update content, invalidate the CDN cache:

### Method 1: CloudFlare Dashboard
1. Go to **Caching → Configuration**
2. Click "Purge Everything" or "Custom Purge"
3. Enter URL patterns to purge

### Method 2: API (Programmatic)

```bash
# Purge everything
curl -X POST "https://api.cloudflare.com/client/v4/zones/YOUR_ZONE_ID/purge_cache" \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"purge_everything":true}'

# Purge specific URLs
curl -X POST "https://api.cloudflare.com/client/v4/zones/YOUR_ZONE_ID/purge_cache" \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"files":["https://yourdomain.com/api/trending/today"]}'
```

## Redis Configuration (For Multi-Server Setups)

If you have multiple backend servers, use Redis for shared caching:

### Option 1: Local Redis (Single Server)

```bash
# Install Redis (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install redis-server

# Start Redis
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Test
redis-cli ping  # Should return "PONG"
```

### Option 2: Redis Cloud (Multi-Server)

Use managed Redis services:
- [Redis Cloud](https://redis.com/redis-enterprise-cloud/)
- [Upstash](https://upstash.com/) (Serverless, free tier available)
- [AWS ElastiCache](https://aws.amazon.com/elasticache/)

### Environment Variables

Add to your `.env` file:

```env
# Redis Configuration
REDIS_URL=redis://localhost:6379/0
ENABLE_REDIS=true

# Cache TTLs (in seconds)
TRENDING_CACHE_TTL=60
SEARCH_CACHE_TTL=30
MEDIA_INFO_CACHE_TTL=86400
```

## Alternative CDNs

### AWS CloudFront

```json
{
  "Comment": "FilmsClub CDN",
  "Origins": [{
    "Id": "filmsclub-origin",
    "DomainName": "your-origin.com",
    "CustomOriginConfig": {
      "HTTPPort": 80,
      "HTTPSPort": 443,
      "OriginProtocolPolicy": "https-only"
    }
  }],
  "DefaultCacheBehavior": {
    "ViewerProtocolPolicy": "redirect-to-https",
    "CachePolicyId": "Managed-CachingOptimized",
    "AllowedMethods": ["GET", "HEAD", "OPTIONS"]
  }
}
```

### KeyCDN

Similar configuration to CloudFlare. Use their API for cache purging:

```bash
curl -X DELETE \
  -H "Authorization: Bearer YOUR_API_KEY" \
  "https://api.keycdn.com/zones/purge/YOUR_ZONE_ID.json"
```

## Monitoring Cache Performance

### Check Cache Hit Ratio

**CloudFlare Analytics**:
- Go to **Analytics → Performance**
- View "Cache Hit Ratio" (target: >80%)

**Custom Monitoring**:
```python
# Add to your admin dashboard
@app.get("/admin/cache-stats")
async def get_cache_stats():
    return {
        "in_memory": await get_all_cache_stats(),
        "redis": await redis_cache.health_check(),
        "cloudflare": {
            "hit_ratio": get_cloudflare_analytics(),
        }
    }
```

### Expected Performance Improvements

| Endpoint | Without CDN | With CloudFlare | Improvement |
|----------|-------------|-----------------|-------------|
| `/api/trending/today` | ~200ms | ~50ms | **4x faster** |
| `/api/media-info/{id}` | ~5000ms | ~50ms | **100x faster** |
| Static Assets | ~100ms | ~20ms | **5x faster** |
| Global Latency | ~300ms | ~50ms | **6x faster** |

## Troubleshooting

### Issue: Cache Not Working

Checklist:
1. ✅ CloudFlare proxy is enabled (orange cloud icon)
2. ✅ Page rules are configured correctly
3. ✅ Backend returns `Cache-Control` headers
4. ✅ No cookies interfering with cache

### Issue: Video Streams Not Working

Videos may have issues with CDN caching:
1. Disable caching for `/dl/*` endpoints
2. Or use signed URLs for video streams
3. Consider using CloudFlare Stream for video delivery

### Issue: Cache Not Invalidating

Solutions:
1. Use specific URL purging instead of "Purge Everything"
2. Add cache-busting query parameters: `?v=2`
3. Reduce TTL for frequently updated content

## Security Considerations

### Prevent Cache Poisoning

1. **Origin Authentication**: Ensure CDN can authenticate with origin
2. **Signed URLs**: Use for sensitive content
3. **WAF Rules**: Enable CloudFlare WAF for DDoS protection

### HTTPS Configuration

Force HTTPS:
1. Go to **SSL/TLS → Overview**
2. Set encryption mode to "Full (strict)"
3. Enable "Always Use HTTPS"

## Cost Optimization

### Free Tier Limits (CloudFlare)

- Unlimited bandwidth (fair use)
- Page Rules: 3 free rules
- Workers: 100,000 requests/day free

### Reducing Bandwidth Costs

1. Enable Brotli compression (better than gzip)
2. Cache static assets aggressively
3. Optimize images before upload
4. Use WebP format for images

## Additional Resources

- [CloudFlare Learning Center](https://www.cloudflare.com/learning/)
- [Redis Best Practices](https://redis.io/docs/management/optimization/)
- [HTTP Caching Guide](https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching)

## Summary Checklist

- [ ] CloudFlare account created
- [ ] Domain DNS configured
- [ ] Page rules added (trending, media-info, search, static)
- [ ] Performance features enabled (Brotli, Auto Minify)
- [ ] Redis installed and configured (if multi-server)
- [ ] Environment variables set (REDIS_URL, ENABLE_REDIS)
- [ ] Cache headers verified
- [ ] Cache invalidation tested
- [ ] Monitoring dashboard set up
- [ ] HTTPS enforced
- [ ] Performance benchmarked

---

**Questions?** Check the FilmsClub GitHub issues or documentation.
