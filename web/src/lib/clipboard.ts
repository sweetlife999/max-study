/** Copies `text`, falling back to a selection-based copy where the async API is unavailable. */
export async function copyText(text: string): Promise<boolean> {
  // `navigator.clipboard` is typed as always present but is missing in insecure contexts.
  const clipboard = navigator.clipboard as Clipboard | undefined;
  try {
    if (clipboard) {
      await clipboard.writeText(text);
      return true;
    }
  } catch {
    // Permission denied or insecure context — fall through to the legacy path.
  }

  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    // The only copy API available in webviews without the async clipboard permission.
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    const copied = document.execCommand('copy');
    area.remove();
    return copied;
  } catch {
    return false;
  }
}
