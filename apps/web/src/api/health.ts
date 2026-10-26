export interface Health {
  readonly status: 'ok';
}

export async function getHealth(signal: AbortSignal): Promise<Health> {
  const response = await fetch('/health', {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error('Health request failed');
  }

  const body: unknown = await response.json();
  if (
    typeof body !== 'object' ||
    body === null ||
    !('status' in body) ||
    body.status !== 'ok'
  ) {
    throw new Error('Invalid health response');
  }
  return { status: body.status };
}
