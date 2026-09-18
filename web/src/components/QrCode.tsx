import { useMemo, type ReactNode } from 'react';

import { buildQrPath } from '../lib/qrMatrix';

export interface QrCodeProps {
  value: string;
  /** Accessible name; the payload itself is meaningless to a screen reader. */
  label: string;
  className?: string;
  fallback?: ReactNode;
}

export function QrCode({ value, label, className, fallback = null }: QrCodeProps) {
  const path = useMemo(() => buildQrPath(value), [value]);
  if (!path) return <>{fallback}</>;

  return (
    <svg
      className={className}
      viewBox={`0 0 ${path.side} ${path.side}`}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
    >
      <rect width={path.side} height={path.side} fill="#ffffff" />
      <path d={path.d} fill="#000000" />
    </svg>
  );
}
