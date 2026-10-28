export function metric(
  value: number | null | undefined,
  unit = '',
  digits = 2,
): string {
  return value === null || value === undefined
    ? 'Unavailable'
    : `${value.toLocaleString('en-US', { maximumFractionDigits: digits })}${unit}`;
}

export function timestamp(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  });
}
