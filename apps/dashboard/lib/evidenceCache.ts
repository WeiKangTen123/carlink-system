/**
 * Dual-tier High-Performance Evidence Cache Manager
 *
 * Tier 1 (L1): In-Memory LRU Object URL Manager
 * - Caps active decoded textures to safe limits (MAX_ACTIVE_OBJECT_URLS = 16)
 * - Auto-revokes older blob Object URLs via URL.revokeObjectURL to prevent Chromium tab crashes
 *
 * Tier 2 (L2): Persistent Browser CacheStorage API ("carlink-evidence-v1")
 * - Stores compressed WebP/JPEG binaries in browser disk cache across reloads
 * - Zero network round-trip latency on subsequent inspections
 *
 * Controlled Concurrency Preloader:
 * - Throttles background fetching to max 2 concurrent requests
 * - Schedules background caching via requestIdleCallback to protect Three.js WebGL frame rates
 */

const CACHE_NAME = "carlink-evidence-v1";
const MAX_ACTIVE_OBJECT_URLS = 16;

class EvidenceCacheManager {
  private memoryLruMap = new Map<string, string>(); // remoteUrl -> objectUrl
  private preloadedSet = new Set<string>();
  private activeFetches = 0;
  private queue: string[] = [];
  private maxConcurrency = 2;

  /**
   * Resolves an image URL through the dual-tier cache.
   * Returns a local blob: URL for instant display, or falls back to remoteUrl on failure.
   */
  public async getCachedUrl(remoteUrl: string): Promise<string> {
    if (!remoteUrl || typeof window === "undefined") {
      return remoteUrl;
    }

    // 1. Check L1 Memory Cache
    const existingObjectUrl = this.memoryLruMap.get(remoteUrl);
    if (existingObjectUrl) {
      // Refresh LRU position (delete & re-insert)
      this.memoryLruMap.delete(remoteUrl);
      this.memoryLruMap.set(remoteUrl, existingObjectUrl);
      return existingObjectUrl;
    }

    // 2. Check L2 Browser CacheStorage API
    if ("caches" in window) {
      try {
        const cache = await caches.open(CACHE_NAME);
        const match = await cache.match(remoteUrl);

        if (match) {
          const blob = await match.blob();
          const objectUrl = URL.createObjectURL(blob);
          this.registerObjectUrl(remoteUrl, objectUrl);
          return objectUrl;
        }

        // Fetch and put into L2 cache
        const res = await fetch(remoteUrl, { mode: "cors", credentials: "omit" });
        if (res.ok) {
          await cache.put(remoteUrl, res.clone());
          const blob = await res.blob();
          const objectUrl = URL.createObjectURL(blob);
          this.registerObjectUrl(remoteUrl, objectUrl);
          return objectUrl;
        }
      } catch (err) {
        console.warn("[EvidenceCache] CacheStorage lookup failed, falling back to direct URL:", err);
      }
    }

    // 3. Fallback to remote URL directly if CacheStorage is unsupported
    return remoteUrl;
  }

  /**
   * Registers a newly generated blob: URL in the LRU memory pool.
   * Evicts and revokes the oldest Object URL if memory limit is reached.
   */
  private registerObjectUrl(remoteUrl: string, objectUrl: string): void {
    if (this.memoryLruMap.size >= MAX_ACTIVE_OBJECT_URLS) {
      const oldestKey = this.memoryLruMap.keys().next().value;
      if (oldestKey) {
        const oldUrl = this.memoryLruMap.get(oldestKey);
        if (oldUrl && oldUrl.startsWith("blob:")) {
          URL.revokeObjectURL(oldUrl);
        }
        this.memoryLruMap.delete(oldestKey);
      }
    }
    this.memoryLruMap.set(remoteUrl, objectUrl);
  }

  /**
   * Queues URLs for non-blocking background caching.
   * Concurrency is throttled to prevent network or GPU starvation.
   */
  public preload(urls: string[]): void {
    if (typeof window === "undefined" || !("caches" in window)) return;

    for (const url of urls) {
      if (!url || this.preloadedSet.has(url)) continue;
      this.preloadedSet.add(url);
      this.queue.push(url);
    }

    this.processQueue();
  }

  private processQueue(): void {
    if (this.activeFetches >= this.maxConcurrency || this.queue.length === 0) {
      return;
    }

    const nextUrl = this.queue.shift();
    if (!nextUrl) return;

    this.activeFetches++;

    const schedule = (cb: () => void) => {
      if ("requestIdleCallback" in window) {
        (window as Window & { requestIdleCallback: (fn: () => void) => void }).requestIdleCallback(cb);
      } else {
        setTimeout(cb, 50);
      }
    };

    schedule(async () => {
      try {
        const cache = await caches.open(CACHE_NAME);
        const match = await cache.match(nextUrl);
        if (!match) {
          const res = await fetch(nextUrl, { mode: "cors", credentials: "omit" });
          if (res.ok) {
            await cache.put(nextUrl, res);
          }
        }
      } catch {
        // Silently tolerate background preload failures
      } finally {
        this.activeFetches--;
        this.processQueue();
      }
    });
  }

  /**
   * Cleans up all memory references on application unmount or case switch.
   */
  public clearMemory(): void {
    for (const objectUrl of this.memoryLruMap.values()) {
      if (objectUrl && objectUrl.startsWith("blob:")) {
        try {
          URL.revokeObjectURL(objectUrl);
        } catch {
          // Ignore revocation errors
        }
      }
    }
    this.memoryLruMap.clear();
  }
}

export const evidenceCache = new EvidenceCacheManager();
