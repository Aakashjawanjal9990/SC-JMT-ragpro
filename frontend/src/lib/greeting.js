const IST = 'Asia/Kolkata';

/** Greeting for the current hour in Indian Standard Time. */
export function getGreeting(date = new Date()) {
  const hour =
    Number(
      new Intl.DateTimeFormat('en-IN', {
        hour: 'numeric',
        hour12: false,
        timeZone: IST,
      }).format(date),
    ) % 24;
  if (hour < 5) return 'Working late';
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  if (hour < 21) return 'Good evening';
  return 'Good night';
}

/** "Happy Thursday!" for the current weekday in IST. */
export function getDayWish(date = new Date()) {
  const day = new Intl.DateTimeFormat('en-IN', {
    weekday: 'long',
    timeZone: IST,
  }).format(date);
  return `Happy ${day}!`;
}

/** "2026-10-22" for today in IST. Used to show the day wish once a day. */
export function istDateKey(date = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: IST }).format(date);
}
