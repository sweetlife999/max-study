/**
 * Saving a file from a MAX mini-app.
 *
 * The Bridge documentation says `<a download>` does not work inside the MAX webview and points
 * to `window.WebApp.downloadFile(url, file_name)` instead. That method makes the native client
 * fetch the URL itself, so it cannot carry the `X-Max-Init-Data` header every endpoint of §7
 * requires — it would save the 401 body as a "CSV". Until the contract offers a signed download
 * URL we therefore fetch the file ourselves (authenticated) and offer it two ways:
 *
 *   1. an object URL clicked through a temporary `<a download>` — works in browsers and in the
 *      web version of MAX;
 *   2. the plain text on screen, so the organizer is never stuck (the caller renders it).
 */

export type SaveOutcome = 'saved' | 'unsupported';

const OBJECT_URL_LIFETIME_MS = 60_000;

/** Offers `blob` to the user as a file named `fileName`. Never throws. */
export function saveBlob(blob: Blob, fileName: string): SaveOutcome {
  if (typeof URL.createObjectURL !== 'function') return 'unsupported';
  let url: string;
  try {
    url = URL.createObjectURL(blob);
  } catch {
    return 'unsupported';
  }

  try {
    const anchor = document.createElement('a');
    if (!('download' in anchor)) {
      URL.revokeObjectURL(url);
      return 'unsupported';
    }
    anchor.href = url;
    anchor.download = fileName;
    anchor.rel = 'noopener';
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } catch {
    URL.revokeObjectURL(url);
    return 'unsupported';
  }

  // Revoking immediately can cancel the download in some webviews.
  setTimeout(() => URL.revokeObjectURL(url), OBJECT_URL_LIFETIME_MS);
  return 'saved';
}

/** `attendance-<id>-<YYYY-MM-DD>.csv` — stable, sortable and safe on every filesystem. */
export function attendanceFileName(eventId: number, now: number = Date.now()): string {
  const day = new Date(now).toISOString().slice(0, 10);
  return `attendance-${eventId}-${day}.csv`;
}
