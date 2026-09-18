/** Small session-only cache, including in-flight de-duplication. Failed reads never persist. */
export class ReadCache {
  private values = new Map<string, {expires: number; value: Promise<unknown>}>();
  private capacity: number;
  private ttlMs: number;
  constructor(capacity = 64, ttlMs = 30_000) { this.capacity=capacity; this.ttlMs=ttlMs; }
  get<T>(key: string, load: () => Promise<T>): Promise<T> {
    const cached = this.values.get(key);
    if (cached && cached.expires > Date.now()) return cached.value as Promise<T>;
    const value = load();
    const entry = {expires: Date.now() + this.ttlMs, value};
    this.values.delete(key);
    this.values.set(key, entry);
    while(this.values.size > this.capacity) this.values.delete(this.values.keys().next().value!);
    void value.catch(() => { if(this.values.get(key) === entry) this.values.delete(key); });
    return value;
  }
  clear() { this.values.clear(); }
}
export const mediaCache = new ReadCache();

/** Cancel only this caller's wait, not the shared request used by other readers. */
export function consumeCached<T>(value: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return value;
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort', abort, {once: true});
    value.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}
