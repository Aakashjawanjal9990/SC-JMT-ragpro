import { useCallback, useEffect, useState } from 'react';

/** Pinned conversation ids, kept in this browser only. */
const read = (key) => {
  try {
    const v = JSON.parse(localStorage.getItem(key) || '{}');
    return { pinned: Array.isArray(v.pinned) ? v.pinned.map(String) : [] };
  } catch {
    return { pinned: [] };
  }
};

export default function useSidebarPrefs(userKey) {
  const key = `nm_sidebar:${userKey || 'anon'}`;
  const [prefs, setPrefs] = useState(() => read(key));

  useEffect(() => {
    setPrefs(read(key));
  }, [key]);

  const togglePin = useCallback(
    (id) => {
      const s = String(id);
      const next = {
        pinned: prefs.pinned.includes(s)
          ? prefs.pinned.filter((x) => x !== s)
          : [...prefs.pinned, s],
      };
      setPrefs(next);
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* storage unavailable: the change lasts until the page reloads */
      }
    },
    [key, prefs],
  );

  return { pinned: prefs.pinned, togglePin };
}
