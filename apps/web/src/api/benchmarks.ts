import {
  array,
  parseDataset,
  parseHistoryDetail,
  parseHistoryItem,
  parseJob,
} from './decode';
import type { RunRequest } from './contracts';

/** A sanitized transport failure with HTTP status and retry delay in milliseconds. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status = 0,
    readonly retryAfter = 1000,
  ) {
    super(message);
  }
}
const messages: Record<number, string> = {
  404: 'This dataset or experiment is no longer available.',
  409: 'This experiment is not ready, or its submission key conflicts. Refresh its status.',
  422: 'The server rejected this configuration. Check the dataset and benchmark settings.',
  429: 'The benchmark queue is full. Wait before submitting again.',
  503: 'Benchmark storage or coordination is unavailable. Try again shortly.',
};
/**
 * Request same-origin JSON with owner cancellation and a ten-second timeout.
 * Error bodies never reach the UI. Retry-After accepts seconds or a date and is
 * bounded to 1–60 seconds; callers own retry scheduling.
 * @param url API route, with caller-controlled identifiers encoded.
 * @param parse Decoder for fields consumed by the dashboard.
 * @param signal Owner cancellation, including unmount and selection changes.
 * @param init HTTP method, body, and headers.
 * @returns Validated application data.
 * @throws {ApiError} Transport, HTTP, JSON, or response validation fails.
 * @throws {Error} Caller cancellation propagates without becoming a transport error.
 */
async function request<T>(
  url: string,
  parse: (body: unknown) => T,
  signal: AbortSignal,
  init: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
      headers: {
        Accept: 'application/json',
        'X-Tachyron-Request': '1',
        ...init.headers,
      },
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError(
      'The API could not be reached within 10 seconds. Check the local server and retry.',
    );
  }
  if (!response.ok) {
    const header = response.headers.get('Retry-After');
    const seconds = header === null ? NaN : Number(header);
    const dateDelay = header === null ? NaN : Date.parse(header) - Date.now();
    const delay = Number.isFinite(seconds) ? seconds * 1000 : dateDelay;
    throw new ApiError(
      messages[response.status] ?? 'The API request failed. Try again.',
      response.status,
      Number.isFinite(delay) ? Math.max(1000, Math.min(delay, 60000)) : 1000,
    );
  }
  try {
    const body: unknown = await response.json();
    return parse(body);
  } catch {
    throw new ApiError(
      'The API returned an invalid response. Retry or check the server version.',
    );
  }
}
/** Read the operator-owned frozen registry without sending filesystem paths. */
export const getDatasets = (signal: AbortSignal) =>
  request('/api/datasets', (body) => array(body, parseDataset), signal);

/** Read 20 terminal comparisons, keeping independent contexts separate. */
export const getHistory = (signal: AbortSignal, offset = 0) =>
  request(
    `/api/benchmarks?limit=20&offset=${offset}`,
    (body) => array(body, parseHistoryItem),
    signal,
  );

export const getDetail = (id: string, signal: AbortSignal) =>
  request(
    `/api/benchmarks/${encodeURIComponent(id)}`,
    parseHistoryDetail,
    signal,
  );

export const getJob = (id: string, signal: AbortSignal) =>
  request(`/api/jobs/${encodeURIComponent(id)}`, parseJob, signal);

/**
 * Submit or recover one idempotent comparison. HTTP 202 means acceptance only.
 * @param body Fixed-pipeline benchmark configuration.
 * @param key Retained identity; retries must reuse the same request body and key.
 * @param signal Submission owner's cancellation.
 * @returns The server's durable job snapshot.
 * @throws {ApiError} Admission, transport, or response validation fails.
 */
export const startJob = (body: RunRequest, key: string, signal: AbortSignal) =>
  request('/api/jobs', parseJob, signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify(body),
  });

/** Request cooperative cancellation; a running batch can still finish first. */
export const cancelJob = (id: string, signal: AbortSignal) =>
  request(`/api/jobs/${encodeURIComponent(id)}/cancel`, parseJob, signal, {
    method: 'POST',
  });
