import redis

url = "redis://default:MkrVLXBzQYfZD3CO5db2leGWCBMpAJP3@tile-stretch-sidewalk-82609.db.redis.io:15287"

try:
    r = redis.Redis.from_url(url)
    print("PING:", r.ping())
except Exception as e:
    print("ERROR:", e)