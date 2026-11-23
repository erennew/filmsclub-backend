const DEFAULT_TTL = 60_000;

/*
 * Requests had no timeout at all, so a slow or stalled backend left the promise
 * pending forever. On the Home route that also pinned the launch splash, because
 * `heroLoading` only cleared when the fetch settled — the app showed the splash
 * for the full MAX_VISIBLE_MS and then the hero skeleton, with no way to tell a
 * hung request from a slow one. Abort after this long so callers always settle.
 *
 * 8s was too tight in practice. The list endpoints fan out across six database
 * clusters, so a cold page could legitimately take longer than that — the
 * request then aborted, SmartSection flipped to "Couldn't load this shelf", and
 * the shelf stayed broken until the visitor hit Retry. The budget now covers a
 * cold fan-out while still bounding a truly hung request.
 *
 * Overridable per call via `getJSON(base, path, params, ttl, { timeoutMs })`.
 */
const DEFAULT_TIMEOUT_MS = 20_000;

/* A request that fails *fast* gets one automatic retry: a dropped connection or
 * a rejected handshake is usually transient, and retrying keeps a whole row from
 * turning into an error card over one blip. A request that runs out the full
 * timeout is deliberately NOT retried — it was genuinely slow, so a second
 * attempt would just double the time the visitor stares at a broken shelf. That
 * keeps the worst case at one timeout instead of two. */
const DEFAULT_RETRIES = 1;
const RETRY_DELAY_MS = 600;

const cache = new Map();

/*
 * Global concurrency cap.
 *
 * The TTL cache already collapses duplicate requests, but it cannot stop a
 * *burst of distinct* ones: the homepage mounts a hero, a Top 10 row and a set
 * of shelves, and each shelf that is visible on first paint used to call
 * getJSON in the same tick. The list endpoints fan out across several database
 * clusters, so ten simultaneous cold requests queue up on the backend and
 * everything — the hero included — arrives late. Measured against the old
 * ten-at-once load, ~5 in flight keeps a cold fan-out saturated without the
 * backend thrashing, which is why the cap is 5 rather than 1 (a strictly
 * serial queue would leave the fan-out idle) or 10 (no cap at all).
 *
 * The limit applies to the network work (requestWithRetry), not to the cache
 * lookup: a cache hit resolves from memory without taking a slot, so revisiting
 * a shelf is never delayed behind unrelated traffic. A slot is held for the
 * whole life of the attempt sequence, including the retry backoff, because the
 * point of the cap is to bound *concurrent work against the backend*.
 *
 * Ordering: the queue is strictly FIFO and duplicate keys are deduped by the
 * cache above, so whoever calls getJSON first is served first. The hero is
 * issued before any shelf on mount, so it is head of the queue and no separate
 * priority tier is needed. Callers see exactly the same promise semantics as
 * before: same resolve value, same rejection, same 20s timeout, same
 * single fast-failure retry, same "don't cache failures" behaviour.
 */
const MAX_CONCURRENT_REQUESTS = 5;

let activeRequests = 0;
const pendingSlots = [];

/** Resolves as soon as a slot is free; the caller must call releaseSlot(). */
function acquireSlot() {
  if (activeRequests < MAX_CONCURRENT_REQUESTS) {
    activeRequests += 1;
    return Promise.resolve();
  }
  return new Promise((resolve) => pendingSlots.push(resolve));
}

/* Hands the slot straight to the next waiter (leaving activeRequests as-is) so
 * the counter can never drift; only an empty queue decrements it. */
function releaseSlot() {
  const next = pendingSlots.shift();
  if (next) next();
  else activeRequests -= 1;
}

/*
 * Runs `task` once a slot is free and releases it however `task` settles —
 * resolve, reject, abort or timeout. `finally` is the important part: an
 * escaping throw must still free the slot, or one bad request would wedge every
 * request behind it.
 */
function withSlot(task) {
  return acquireSlot().then(async () => {
    try {
      return await task();
    } finally {
      releaseSlot();
    }
  });
}

const keyFor = (base, path, params) => {
  const stable = Object.keys(params)
    .sort()
    .map((k) => [k, params[k]]);
  return `${base}${path}|${JSON.stringify(stable)}`;
};

export function clearJSONCache() {
  cache.clear();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/*
 * Only transport-level failures are worth retrying. A 404 or a 400 will fail
 * the same way every time, so retrying just delays the error state.
 */
const isRetryable = (error) => {
  if (!error) return false;
  if (error.name === "TimeoutError") return false;
  if (error.name === "AbortError") return false;
  // fetch() rejects with a TypeError when the request never reached the server.
  if (error.name === "TypeError") return true;
  return /network|failed to fetch|load failed/i.test(error.message || "");
};

function requestOnce(url, timeoutMs) {
  // AbortSignal.timeout is not available everywhere we run (older iOS Safari), so
  // fall back to a manual controller. The timer is always cleared: leaving it
  // running would keep the promise's closure alive for the whole timeout.
  const controller =
    typeof AbortController === "function" ? new AbortController() : null;

  /* Normalise the abort into a named error so the retry check does not have to
   * guess: a manual controller.abort() surfaces as AbortError while
   * AbortSignal.timeout() surfaces as TimeoutError. */
  const timer = controller
    ? setTimeout(() => {
        const err = new Error(`Request timed out after ${timeoutMs}ms`);
        err.name = "TimeoutError";
        controller.abort(err);
      }, timeoutMs)
    : null;

  return fetch(url, controller ? { signal: controller.signal } : {})
    .then(async (response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return { data: await response.json() };
    })
    .finally(() => {
      if (timer) clearTimeout(timer);
    });
}

async function requestWithRetry(url, timeoutMs, retries) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await requestOnce(url, timeoutMs);
    } catch (error) {
      lastError = error;
      if (attempt === retries || !isRetryable(error)) throw error;
      await sleep(RETRY_DELAY_MS * (attempt + 1));
    }
  }
  throw lastError;
}

export function getJSON(base, path, params = {}, ttl = DEFAULT_TTL, options = {}) {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const retries = options.retries ?? DEFAULT_RETRIES;
  const key = keyFor(base, path, params);
  const now = Date.now();
  const hit = cache.get(key);
  if (hit && hit.expires > now) return hit.promise;

  const url = new URL(
    `${base}${path}`,
    globalThis.window?.location?.href ?? "http://localhost"
  );
  Object.entries(params).forEach(([name, value]) => {
    if (value !== undefined && value !== null) url.searchParams.set(name, value);
  });

  // The network work runs under the slot scheduler; the promise handed to the
  // caller (and stored in the cache, so identical concurrent calls still share
  // it) is unchanged in shape and settle behaviour.
  const promise = withSlot(() =>
    requestWithRetry(url, timeoutMs, retries)
  ).catch((error) => {
    // A failed entry must not be cached, or the retry button would replay the
    // rejection for the rest of the TTL instead of hitting the network.
    if (cache.get(key)?.promise === promise) cache.delete(key);
    throw error;
  });

  // Set synchronously, before any await, so a second identical call in the same
  // tick finds this promise and dedupes instead of queueing a second request.
  cache.set(key, { promise, expires: now + ttl });
  return promise;
}
