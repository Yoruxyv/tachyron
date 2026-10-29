import { ApiError } from './benchmarks';
import type { Dataset } from './contracts';
import { parseDataset } from './decode';

export interface ImportPolicy {
  max_files: number;
  max_file_bytes: number;
  max_total_bytes: number;
  metadata_bytes: number;
  receive_seconds: number;
  image_seconds: number;
}
export interface ImportOperation {
  operation_id: string;
  state: 'RECEIVING' | 'VALIDATING' | 'READY' | 'FAILED' | 'CANCELLED';
  cancel_requested: boolean;
  progress: {
    total_files: number | null;
    received_files: number | null;
    validated_files: number | null;
  };
  dataset: Dataset | null;
  error_code: string | null;
}
export const importTerminal = (operation: ImportOperation) =>
  ['READY', 'FAILED', 'CANCELLED'].includes(operation.state);
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid import response.');
  return value as Record<string, unknown>;
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
    throw new Error('Invalid import count.');
  return value;
}
export function parseImport(value: unknown): ImportOperation {
  const data = record(value);
  const progress = record(data.progress);
  const state = data.state;
  if (
    typeof data.operation_id !== 'string' ||
    !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/u.test(data.operation_id) ||
    (state !== 'RECEIVING' &&
      state !== 'VALIDATING' &&
      state !== 'READY' &&
      state !== 'FAILED' &&
      state !== 'CANCELLED') ||
    typeof data.cancel_requested !== 'boolean' ||
    (data.error_code !== null && typeof data.error_code !== 'string')
  )
    throw new Error('Invalid import response.');
  const dataset = data.dataset === null ? null : parseDataset(data.dataset);
  if ((state === 'READY') !== Boolean(dataset))
    throw new Error('Invalid import registration.');
  return {
    operation_id: data.operation_id,
    state,
    cancel_requested: data.cancel_requested,
    dataset,
    error_code: data.error_code,
    progress: {
      total_files:
        progress.total_files === null ? null : count(progress.total_files),
      received_files:
        progress.received_files === null
          ? null
          : count(progress.received_files),
      validated_files:
        progress.validated_files === null
          ? null
          : count(progress.validated_files),
    },
  };
}
const messages: Record<string, string> = {
  invalid_path: 'A selected path is not portable or safe.',
  path_conflict: 'Selected paths conflict by name, case, or Unicode spelling.',
  invalid_image:
    'A selected image cannot be decoded. Review the files and start a new import.',
  unsupported_image: 'A selected image mode or frame count is unsupported.',
  resource_limit: 'An image exceeds the processing safety limits.',
  import_busy: 'Another import is active. Wait and try again.',
  dataset_id_conflict:
    'This import identifier is already registered. Choose the folder again.',
  workload_busy: 'A benchmark owns this host. Wait until it finishes.',
  upload_limit_exceeded: 'The upload exceeds a file or request limit.',
  dataset_capacity: 'Managed dataset capacity is full.',
  catalog_capacity: 'The permanent import receipt catalog is full.',
  upload_timeout: 'The upload timed out. Check its recorded status.',
  validation_timeout:
    'Image validation stalled. Check the failed receipt before starting another import.',
  request_forbidden:
    'The local API rejected this browser origin or request header.',
};
export const importMessage = (code: string | null) =>
  messages[code ?? ''] ??
  'The import failed. Check its status before starting another operation.';
async function failure(response: Response): Promise<never> {
  let code = '';
  try {
    const data = record(await response.json());
    if (typeof data.detail === 'string') code = data.detail;
  } catch {
    /* Untrusted error text is never displayed. */
  }
  throw new ApiError(importMessage(code), response.status, 1000, code);
}
async function jsonRequest(
  url: string,
  signal: AbortSignal,
  method = 'GET',
): Promise<unknown> {
  const response = await fetch(url, {
    method,
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
    headers: { 'X-Tachyron-Request': '1', Accept: 'application/json' },
  });
  if (!response.ok) return failure(response);
  const body: unknown = await response.json();
  return body;
}
export async function getImportPolicy(
  signal: AbortSignal,
): Promise<ImportPolicy> {
  const data = record(await jsonRequest('/api/dataset-imports/policy', signal));
  return {
    max_files: count(data.max_files),
    max_file_bytes: count(data.max_file_bytes),
    max_total_bytes: count(data.max_total_bytes),
    metadata_bytes: count(data.metadata_bytes),
    receive_seconds: count(data.receive_seconds),
    image_seconds: count(data.image_seconds),
  };
}
export async function getImport(id: string, signal: AbortSignal) {
  return parseImport(
    await jsonRequest(`/api/dataset-imports/${encodeURIComponent(id)}`, signal),
  );
}
export async function cancelImport(id: string, signal: AbortSignal) {
  return parseImport(
    await jsonRequest(
      `/api/dataset-imports/${encodeURIComponent(id)}/cancel`,
      signal,
      'POST',
    ),
  );
}
/** Upload progress describes transfer; only a READY receipt confirms registration. */
export function uploadImport(
  body: FormData,
  id: string,
  digest: string,
  timeout: number,
  signal: AbortSignal,
  progress: (value: number | null) => void,
): Promise<ImportOperation> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const clean = () => signal.removeEventListener('abort', abort);
    xhr.upload.onprogress = (event) =>
      progress(event.lengthComputable ? event.loaded / event.total : null);
    xhr.open('POST', '/api/dataset-imports');
    xhr.timeout = timeout;
    xhr.setRequestHeader('X-Tachyron-Request', '1');
    xhr.setRequestHeader('Idempotency-Key', id);
    xhr.setRequestHeader('X-Tachyron-Metadata-SHA256', digest);
    xhr.onerror = xhr.ontimeout = () => {
      clean();
      reject(
        new ApiError(
          'Upload response unconfirmed. Check the operation status.',
        ),
      );
    };
    xhr.onabort = () => {
      clean();
      reject(new DOMException('Upload stopped', 'AbortError'));
    };
    xhr.onload = () => {
      clean();
      try {
        const value: unknown = JSON.parse(xhr.responseText);
        if (xhr.status < 200 || xhr.status >= 300) {
          const data = record(value);
          reject(
            new ApiError(
              importMessage(
                typeof data.detail === 'string' ? data.detail : null,
              ),
              xhr.status,
              1000,
              typeof data.detail === 'string' ? data.detail : '',
            ),
          );
        } else resolve(parseImport(value));
      } catch {
        reject(
          new ApiError(
            'Import response unavailable. Check the operation status.',
          ),
        );
      }
    };
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) {
      clean();
      reject(new DOMException('Upload stopped', 'AbortError'));
      return;
    }
    xhr.send(body);
  });
}
