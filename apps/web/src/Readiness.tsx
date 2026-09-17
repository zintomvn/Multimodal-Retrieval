import { useEffect, useState } from 'react';
import { getReadiness } from './api/client';

export function Readiness() {
  const [value,setValue] = useState<Awaited<ReturnType<typeof getReadiness>> | null>(null);
  const [error,setError] = useState('');
  const [attempt,setAttempt] = useState(0);
  useEffect(()=>{
    const controller = new AbortController();
    setError('');
    getReadiness(controller.signal).then(setValue).catch(e=>{if(!controller.signal.aborted)setError(String(e));});
    return ()=>controller.abort();
  },[attempt]);
  return <details className="readiness"><summary>Service health: {error ? 'check failed' : value?.status ?? 'checking'}</summary>
    {error && <p role="alert">{error}</p>}
    <ul>{Object.entries(value?.checks ?? {}).map(([name,check])=><li key={name}>{name}: <strong>{check.status}</strong>{check.reason ? ` — ${check.reason}` : ''}</li>)}</ul>
    <p>Readiness is cached for 15 seconds. Model configuration alone does not verify inference.</p>
    <button type="button" onClick={()=>setAttempt(n=>n+1)}>Refresh service health</button>
  </details>;
}
