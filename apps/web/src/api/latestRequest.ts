/** Owns one UI request. Abort is best-effort; identity guards late responses. */
export class LatestRequest {
  private active: AbortController | null = null;

  begin(): AbortController | null {
    if (this.active) return null;
    this.active = new AbortController();
    return this.active;
  }

  isCurrent(request: AbortController): boolean {
    return this.active === request && !request.signal.aborted;
  }

  finish(request: AbortController): boolean {
    if (!this.isCurrent(request)) return false;
    this.active = null;
    return true;
  }

  cancel(): void {
    this.active?.abort();
    this.active = null;
  }
}
