import { useEffect, useRef } from 'react';

export function useVisibilityAwareInterval(
  callback: () => void | Promise<void>,
  intervalMs: number,
  enabled = true,
): void {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    if (!enabled) return;

    let timer: ReturnType<typeof setInterval> | undefined;

    const run = () => {
      void cbRef.current();
    };

    const start = () => {
      run();
      timer = setInterval(run, intervalMs);
    };

    const stop = () => {
      if (timer) clearInterval(timer);
      timer = undefined;
    };

    const onVis = () => {
      if (document.visibilityState === 'visible') {
        stop();
        start();
      } else {
        stop();
      }
    };

    if (document.visibilityState === 'visible') {
      start();
    }

    document.addEventListener('visibilitychange', onVis);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      stop();
    };
  }, [intervalMs, enabled]);
}
