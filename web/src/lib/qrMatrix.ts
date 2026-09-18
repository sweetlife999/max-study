import { create } from 'qrcode';

/** Quiet zone required by the QR spec, in modules. */
export const QR_QUIET_ZONE = 4;

export interface QrMatrixPath {
  /** Side of the viewBox in modules, quiet zone included. */
  side: number;
  /** SVG path covering every dark module. */
  d: string;
}

/**
 * Builds the SVG path of a QR symbol. Rendering real SVG elements (instead of a canvas or an
 * HTML string) keeps the component pure, testable under jsdom and free of `innerHTML`.
 * Returns null when the payload cannot be encoded.
 */
export function buildQrPath(value: string): QrMatrixPath | null {
  if (value === '') return null;
  let modules;
  try {
    ({ modules } = create(value, { errorCorrectionLevel: 'M' }));
  } catch {
    return null;
  }
  const { size, data } = modules;
  const parts: string[] = [];
  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      if (data[row * size + col]) {
        parts.push(`M${col + QR_QUIET_ZONE} ${row + QR_QUIET_ZONE}h1v1h-1z`);
      }
    }
  }
  return { side: size + QR_QUIET_ZONE * 2, d: parts.join('') };
}
