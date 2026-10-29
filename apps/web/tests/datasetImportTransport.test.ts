import { beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/benchmarks';
import {
  cancelImport,
  getImportPolicy,
  parseImport,
  uploadImport,
} from '@/api/datasetImports';
import { dataset } from './fixtures';

const id = '12345678-1234-4234-8234-123456789abc';
const receipt = {
  operation_id: id,
  state: 'READY',
  cancel_requested: false,
  progress: { total_files: 1, received_files: 1, validated_files: 1 },
  dataset,
  error_code: null,
};
class UploadRequest {
  static readonly requests: UploadRequest[] = [];
  readonly upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onabort: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  status = 0;
  responseText = '';
  timeout = 0;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn();
  constructor() {
    UploadRequest.requests.push(this);
  }
  abort() {
    this.onabort?.();
  }
}
beforeEach(() => {
  UploadRequest.requests.length = 0;
  vi.stubGlobal('XMLHttpRequest', UploadRequest);
});
function upload(signal = new AbortController().signal) {
  const progress = vi.fn();
  const result = uploadImport(
    new FormData(),
    id,
    'a'.repeat(64),
    900000,
    signal,
    progress,
  );
  const xhr = UploadRequest.requests.at(-1);
  if (!xhr) throw new Error('XHR was not created');
  return { result, xhr, progress };
}
it('reports real transfer progress and preserves the separate upload deadline/headers', async () => {
  const { result, xhr, progress } = upload();
  expect(xhr.timeout).toBe(900000);
  expect(xhr.setRequestHeader).toHaveBeenCalledWith('X-Tachyron-Request', '1');
  expect(xhr.setRequestHeader).toHaveBeenCalledWith('Idempotency-Key', id);
  expect(xhr.setRequestHeader).toHaveBeenCalledWith(
    'X-Tachyron-Metadata-SHA256',
    'a'.repeat(64),
  );
  expect(xhr.setRequestHeader).not.toHaveBeenCalledWith(
    'Content-Type',
    expect.anything(),
  );
  xhr.upload.onprogress?.(
    new ProgressEvent('progress', {
      lengthComputable: true,
      loaded: 5,
      total: 20,
    }),
  );
  expect(progress).toHaveBeenLastCalledWith(0.25);
  xhr.upload.onprogress?.(new ProgressEvent('progress'));
  expect(progress).toHaveBeenLastCalledWith(null);
  xhr.status = 201;
  xhr.responseText = JSON.stringify(receipt);
  xhr.onload?.();
  await expect(result).resolves.toEqual(receipt);
});
it.each([false, true])(
  'aborts before or during transfer without a second request (already aborted: %s)',
  async (already) => {
    const controller = new AbortController();
    if (already) controller.abort();
    const { result, xhr } = upload(controller.signal);
    const rejection = expect(result).rejects.toMatchObject({
      name: 'AbortError',
    });
    controller.abort();
    await rejection;
    expect(xhr.send).toHaveBeenCalledTimes(already ? 0 : 1);
    expect(UploadRequest.requests).toHaveLength(1);
  },
);
it.each(['onerror', 'ontimeout'] as const)(
  'keeps transfer failures unconfirmed for recovery (%s)',
  async (event) => {
    const { result, xhr } = upload();
    xhr[event]?.();
    await expect(result).rejects.toMatchObject({
      status: 0,
      code: '',
      message: 'Upload response unconfirmed. Check the operation status.',
    });
  },
);
it('retains a safe server rejection code without displaying reflected details', async () => {
  const { result, xhr } = upload();
  xhr.status = 429;
  xhr.responseText = JSON.stringify({
    detail: 'import_busy',
    debug: 'C:/private',
  });
  xhr.onload?.();
  await expect(result).rejects.toMatchObject({
    status: 429,
    code: 'import_busy',
    message: 'Another import is active. Wait and try again.',
  });
});
it('does not turn malformed proxy responses into proof of non-admission', async () => {
  const { result, xhr } = upload();
  xhr.status = 503;
  xhr.responseText = '<html>C:/private</html>';
  xhr.onload?.();
  await expect(result).rejects.toMatchObject({ status: 0, code: '' });
});
it.each([undefined, -1, 0.5, '524288', null])(
  'rejects invalid metadata policy without a browser fallback (%j)',
  async (metadata_bytes) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            max_files: 1,
            max_file_bytes: 1024,
            max_total_bytes: 1024,
            metadata_bytes,
            receive_seconds: 7,
            image_seconds: 3,
          }),
        ),
      ),
    );
    await expect(getImportPolicy(new AbortController().signal)).rejects.toThrow(
      'Invalid import count.',
    );
  },
);

it('decodes policy and cancellation through the protected JSON transport', async () => {
  const policy = {
    max_files: 1000,
    max_file_bytes: 33554432,
    max_total_bytes: 536870912,
    metadata_bytes: 524288,
    receive_seconds: 600,
    image_seconds: 60,
  };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(policy)))
    .mockResolvedValueOnce(new Response(JSON.stringify(receipt)));
  vi.stubGlobal('fetch', fetcher);
  const signal = new AbortController().signal;
  expect(await getImportPolicy(signal)).toEqual(policy);
  expect(await cancelImport(id, signal)).toEqual(receipt);
  expect(fetcher).toHaveBeenLastCalledWith(
    `/api/dataset-imports/${id}/cancel`,
    expect.objectContaining({
      method: 'POST',
      headers: { 'X-Tachyron-Request': '1', Accept: 'application/json' },
    }),
  );
});
it('rejects inconsistent registration and malformed progress rather than inventing values', () => {
  expect(() => parseImport({ ...receipt, dataset: null })).toThrow(
    'Invalid import registration.',
  );
  expect(() =>
    parseImport({
      ...receipt,
      progress: { ...receipt.progress, validated_files: true },
    }),
  ).toThrow('Invalid import count.');
});
it('sanitizes JSON-route rejections and retains their status/code', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ detail: 'workload_busy', private: 'C:/private' }),
          { status: 409 },
        ),
      ),
  );
  await expect(cancelImport(id, new AbortController().signal)).rejects.toEqual(
    new ApiError(
      'A benchmark owns this host. Wait until it finishes.',
      409,
      1000,
      'workload_busy',
    ),
  );
});
