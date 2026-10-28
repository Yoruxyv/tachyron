export function metric(
  value: number | null | undefined,
  unit = '',
  digits = 2,
  minimumDigits = 0,
): string {
  return value === null || value === undefined
    ? 'Unavailable'
    : `${value.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: minimumDigits })}${unit}`;
}

export function timestamp(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  });
}
