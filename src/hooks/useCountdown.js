import { useEffect, useState } from 'react';

/** Seconds left until a resend is allowed; call restart() after resending. */
export default function useCountdown(start = 30) {
  const [left, setLeft] = useState(start);

  useEffect(() => {
    if (left <= 0) return undefined;
    const timer = setTimeout(() => setLeft(left - 1), 1000);
    return () => clearTimeout(timer);
  }, [left]);

  return [left, () => setLeft(start)];
}
