import {useCallback,useLayoutEffect,useRef} from 'react';
export function useStableEvent<T extends (...args: any[])=>any>(callback:T):T {
  const ref=useRef(callback);
  useLayoutEffect(()=>{ref.current=callback;});
  return useCallback(((...args:Parameters<T>)=>ref.current(...args)) as T,[]);
}
