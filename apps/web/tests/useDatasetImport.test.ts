import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/benchmarks';
import * as api from '@/api/datasetImports';
import type { ImportOperation } from '@/api/datasetImports';
import { useDatasetImport } from '@/features/datasets/hooks/useDatasetImport';
import { dataset } from './fixtures';
import { imageFile } from './importFixtures';

const id = '12345678-1234-4234-8234-123456789abc';
function receipt(state: ImportOperation['state']): ImportOperation {
  return {
    operation_id: id,
    state,
    cancel_requested: false,
    progress: { total_files: 1, received_files: 1, validated_files: 0 },
    dataset: state === 'READY' ? dataset : null,
    error_code: null,
  };
}
function selectedFile() {
  return imageFile('a.png');
}
class PreparationWorker {
  static readonly instances: PreparationWorker[] = [];
  static get current() {
    return this.instances.at(-1);
  }
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onerror: (() => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() {
    PreparationWorker.instances.push(this);
  }
  complete() {
    this.onmessage?.(
      new MessageEvent('message', {
        data: { kind: 'complete', metadata: '{}', digest: 'a'.repeat(64) },
      }),
    );
  }
}
beforeEach(() => {
  sessionStorage.clear();
  PreparationWorker.instances.length = 0;
  vi.stubGlobal('Worker', PreparationWorker);
  vi.spyOn(crypto, 'randomUUID').mockReturnValue(id);
  vi.spyOn(api, 'getImportPolicy').mockResolvedValue({
    max_files: 1000,
    max_file_bytes: 33554432,
    max_total_bytes: 536870912,
    metadata_bytes: 524288,
    receive_seconds: 600,
    image_seconds: 60,
  });
});

it.each([false, true])(
  'checks UTF-8 metadata bytes against server policy (fits: %s)',
  async (fits) => {
    const metadata = '{"name":"é"}';
    const bytes = new TextEncoder().encode(metadata).length;
    vi.mocked(api.getImportPolicy).mockResolvedValue({
      max_files: 1,
      max_file_bytes: 1024,
      max_total_bytes: 1024,
      metadata_bytes: fits ? bytes : bytes - 1,
      receive_seconds: 7,
      image_seconds: 3,
    });
    vi.spyOn(api, 'getImport').mockImplementation(() => new Promise(() => {}));
    const upload = vi
      .spyOn(api, 'uploadImport')
      .mockResolvedValue(receipt('READY'));
    const { result } = renderHook(() => useDatasetImport(vi.fn()));
    await waitFor(() => expect(result.current.policy).not.toBeNull());
    await act(() => result.current.choose([selectedFile()]));
    act(() => result.current.start());
    act(() =>
      PreparationWorker.current?.onmessage?.(
        new MessageEvent('message', {
          data: { kind: 'complete', metadata, digest: 'a'.repeat(64) },
        }),
      ),
    );
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(
      PreparationWorker.current?.terminate,
    ).toHaveBeenCalledExactlyOnceWith();
    if (fits) {
      expect(upload).toHaveBeenCalledTimes(1);
      expect(upload.mock.calls[0]?.[3]).toBe((7 + 3 * 2 + 30) * 1000);
      expect(result.current.operation?.state).toBe('READY');
    } else {
      expect(upload).not.toHaveBeenCalled();
      expect(result.current.identifier).toBe('');
      expect(result.current.error).toBe(
        'Selection metadata is too large. Choose a smaller folder.',
      );
    }
  },
);

it.each([false, true])(
  'ignores a late cancel response after READY or a newer folder (new folder: %s)',
  async (newFolder) => {
    sessionStorage.setItem('tachyron.import.v1', id);
    vi.spyOn(api, 'getImport').mockResolvedValue(receipt('VALIDATING'));
    let resolveCancel: ((value: ImportOperation) => void) | undefined;
    vi.spyOn(api, 'cancelImport').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCancel = resolve;
        }),
    );
    const ready = vi.fn();
    const { result } = renderHook(() => useDatasetImport(ready));
    await waitFor(() =>
      expect(result.current.operation?.state).toBe('VALIDATING'),
    );
    let cancellation: Promise<void> | undefined;
    act(() => {
      cancellation = result.current.cancel();
    });
    vi.mocked(api.getImport).mockResolvedValue(receipt('READY'));
    act(() => result.current.check());
    await waitFor(() => expect(result.current.operation?.state).toBe('READY'));
    if (newFolder) await act(() => result.current.choose([selectedFile()]));
    await act(async () => {
      resolveCancel?.(receipt(newFolder ? 'READY' : 'RECEIVING'));
      await cancellation;
    });
    expect(result.current.operation?.state ?? null).toBe(
      newFolder ? null : 'READY',
    );
    expect(ready).toHaveBeenCalledTimes(1);
    expect(result.current.busy).toBe(false);
  },
);

it('releases a rejected operation only after an HTTP response and confirmed missing receipt', async () => {
  vi.spyOn(api, 'getImport').mockRejectedValue(new ApiError('missing', 404));
  vi.spyOn(api, 'uploadImport').mockRejectedValue(
    new ApiError('Another import is active.', 429, 1000, 'import_busy'),
  );
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() => result.current.choose([selectedFile()]));
  act(() => result.current.start());
  expect(result.current.preparing).toBe(true);
  expect(PreparationWorker.current).toBeDefined();
  act(() => PreparationWorker.current?.complete());
  expect(api.uploadImport).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(result.current.uploading).toBe(false));
  expect(result.current.identifier).toBe('');
  expect(result.current.busy).toBe(false);
  expect(sessionStorage.getItem('tachyron.import.v1')).toBeNull();
});

it('does not replace READY with an earlier in-flight status snapshot', async () => {
  let completeUpload: ((value: ImportOperation) => void) | undefined;
  let completeStatus: ((value: ImportOperation) => void) | undefined;
  vi.spyOn(api, 'getImport').mockImplementation(
    () =>
      new Promise((resolve) => {
        completeStatus = resolve;
      }),
  );
  vi.spyOn(api, 'uploadImport').mockImplementation(
    () =>
      new Promise((resolve) => {
        completeUpload = resolve;
      }),
  );
  const ready = vi.fn();
  const { result } = renderHook(() => useDatasetImport(ready));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() => result.current.choose([selectedFile()]));
  act(() => result.current.start());
  act(() => PreparationWorker.current?.complete());
  expect(api.getImport).toHaveBeenCalledTimes(1);
  await act(async () => {
    completeUpload?.(receipt('READY'));
    await Promise.resolve();
    completeStatus?.(receipt('RECEIVING'));
  });
  expect(result.current.operation?.state).toBe('READY');
  expect(result.current.busy).toBe(false);
  expect(ready).toHaveBeenCalledTimes(1);
});

it('releases preparation state if the browser cannot construct its worker', async () => {
  vi.stubGlobal(
    'Worker',
    class {
      constructor() {
        throw new DOMException('Blocked by browser', 'SecurityError');
      }
    },
  );
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() => result.current.choose([selectedFile()]));
  act(() => result.current.start());
  expect(result.current.busy).toBe(false);
  expect(result.current.error).toBe(
    'Unable to prepare files. Choose the folder again.',
  );
  expect(sessionStorage.getItem('tachyron.import.v1')).toBeNull();
});

it.each([new ApiError('Response lost'), new ApiError('Proxy failure', 503)])(
  'retains an unconfirmed operation rather than silently retrying (%s)',
  async (failure) => {
    vi.spyOn(api, 'getImport').mockRejectedValue(new ApiError('missing', 404));
    vi.spyOn(api, 'uploadImport').mockRejectedValue(failure);
    const { result } = renderHook(() => useDatasetImport(vi.fn()));
    await waitFor(() => expect(result.current.policy).not.toBeNull());
    await act(() => result.current.choose([selectedFile()]));
    act(() => result.current.start());
    act(() => PreparationWorker.current?.complete());
    await waitFor(() => expect(result.current.uploading).toBe(false));
    expect(result.current.identifier).toBe(id);
    expect(result.current.busy).toBe(true);
    expect(sessionStorage.getItem('tachyron.import.v1')).toBe(id);
    expect(api.uploadImport).toHaveBeenCalledTimes(1);
  },
);

it('cancels preparation without uploading and ignores its queued callbacks', async () => {
  const upload = vi.spyOn(api, 'uploadImport');
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() => result.current.choose([selectedFile()]));
  act(() => result.current.start());
  const worker = PreparationWorker.current;
  expect(worker).toBeDefined();
  await act(() => result.current.cancel());
  await act(() => result.current.choose([selectedFile()]));
  act(() => {
    worker?.complete();
    worker?.onerror?.();
  });
  expect(result.current.busy).toBe(false);
  expect(result.current.operation).toBeNull();
  expect(result.current.error).toBe('');
  expect(worker?.terminate).toHaveBeenCalled();
  expect(upload).not.toHaveBeenCalled();
});

it('cleans up a worker when posting selected files fails', async () => {
  vi.stubGlobal(
    'Worker',
    class extends PreparationWorker {
      constructor() {
        super();
        this.postMessage.mockImplementation(() => {
          throw new DOMException('Clone failed', 'DataCloneError');
        });
      }
    },
  );
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() => result.current.choose([selectedFile()]));
  act(() => result.current.start());
  expect(result.current.busy).toBe(false);
  expect(PreparationWorker.current?.terminate).toHaveBeenCalledTimes(1);
  expect(result.current.error).toBe(
    'Unable to prepare files. Choose the folder again.',
  );
});

it('keeps cancellation failures unconfirmed until status verifies the outcome', async () => {
  sessionStorage.setItem('tachyron.import.v1', id);
  vi.spyOn(api, 'getImport').mockResolvedValue(receipt('VALIDATING'));
  vi.spyOn(api, 'cancelImport').mockRejectedValue(new Error('C:/private'));
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  await waitFor(() =>
    expect(result.current.operation?.state).toBe('VALIDATING'),
  );
  await act(() => result.current.cancel());
  expect(result.current.error).toBe(
    'Cancellation unconfirmed. Check operation status.',
  );
  expect(result.current.operation?.state).toBe('VALIDATING');
  expect(result.current.busy).toBe(true);
  expect(sessionStorage.getItem('tachyron.import.v1')).toBe(id);
});

it('recovers a committed registration after POST returns a cleanup failure', async () => {
  const fetchReceipt = vi
    .spyOn(api, 'getImport')
    .mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValue(receipt('READY'));
  vi.spyOn(api, 'uploadImport').mockRejectedValue(
    new ApiError('Worker cleanup unavailable', 503, 1000, 'worker_unavailable'),
  );
  const ready = vi.fn();
  const { result } = renderHook(() => useDatasetImport(ready));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() => result.current.choose([selectedFile()]));
  act(() => result.current.start());
  act(() => PreparationWorker.current?.complete());
  await waitFor(() => expect(result.current.operation?.state).toBe('READY'));
  expect(fetchReceipt).toHaveBeenCalledTimes(2);
  expect(ready).toHaveBeenCalledExactlyOnceWith(dataset);
  expect(result.current.identifier).toBe(id);
  expect(result.current.busy).toBe(false);
  expect(result.current.error).toBe('');
});

it('pauses failed polling and resumes the same operation without uploading again', async () => {
  vi.useFakeTimers();
  sessionStorage.setItem('tachyron.import.v1', id);
  const status = vi
    .spyOn(api, 'getImport')
    .mockRejectedValue(new ApiError('Offline'));
  const upload = vi.spyOn(api, 'uploadImport');
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(status).toHaveBeenCalledTimes(5);
  expect(result.current.polling).toBe(false);
  expect(result.current.busy).toBe(true);
  expect(result.current.identifier).toBe(id);
  status.mockResolvedValue(receipt('CANCELLED'));
  act(() => result.current.check());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.operation?.state).toBe('CANCELLED');
  expect(result.current.busy).toBe(false);
  expect(upload).not.toHaveBeenCalled();
});

it('does not hash or upload an unsupported folder even if an entry is explicitly toggled off', async () => {
  const upload = vi.spyOn(api, 'uploadImport');
  const ready = vi.fn();
  const { result } = renderHook(() => useDatasetImport(ready));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() =>
    result.current.choose([imageFile(), imageFile('report.pdf')]),
  );
  const index = result.current.files.findIndex((entry) => entry.unsupported);
  expect(index).toBeGreaterThanOrEqual(0);
  act(() => result.current.toggle(index));
  act(() => result.current.start());
  expect(PreparationWorker.instances).toHaveLength(0);
  expect(upload).not.toHaveBeenCalled();
  expect(ready).not.toHaveBeenCalled();
});

it('ignores a slow rejected inspection after another valid folder is selected', async () => {
  const slow = imageFile('spoof.jpg');
  let finish: ((value: ArrayBuffer) => void) | undefined;
  const blob = slow.slice();
  Object.defineProperty(blob, 'arrayBuffer', {
    value: () =>
      new Promise<ArrayBuffer>((resolve) => {
        finish = resolve;
      }),
  });
  vi.mocked(slow.slice).mockReturnValue(blob);
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  let previous: Promise<void> | undefined;
  act(() => {
    previous = result.current.choose([slow]);
  });
  await act(() => result.current.choose([imageFile('new.png')]));
  await act(async () => {
    finish?.(new Uint8Array([37, 80, 68, 70]).buffer);
    await previous;
  });
  expect(result.current.files.map((entry) => entry.file.name)).toEqual([
    'new.png',
  ]);
  expect(result.current.error).toBe('');
  expect(result.current.feedback).toBeNull();
});

it('keeps count, aggregate byte and per-file limits before creating a hashing worker', async () => {
  vi.mocked(api.getImportPolicy).mockResolvedValue({
    max_files: 1,
    max_file_bytes: 8,
    max_total_bytes: 8,
    metadata_bytes: 524288,
    receive_seconds: 600,
    image_seconds: 60,
  });
  const { result } = renderHook(() => useDatasetImport(vi.fn()));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() =>
    result.current.choose([imageFile('a.png'), imageFile('b.png')]),
  );
  act(() => result.current.start());
  expect(PreparationWorker.instances).toHaveLength(0);
  act(() => result.current.toggle(0));
  act(() => result.current.start());
  expect(PreparationWorker.instances).toHaveLength(1);
  await act(() => result.current.cancel());
  await act(() =>
    result.current.choose([
      imageFile('large.png', [137, 80, 78, 71, 13, 10, 26, 10, 0]),
    ]),
  );
  expect(result.current.files[0]?.problem).toBe(
    'File size exceeds import limits',
  );
  act(() => result.current.start());
  expect(PreparationWorker.instances).toHaveLength(1);
});

it('emits success only for a newly initiated READY operation, closes review and retains its receipt', async () => {
  vi.spyOn(api, 'getImport').mockImplementation(() => new Promise(() => {}));
  vi.spyOn(api, 'uploadImport').mockResolvedValue(receipt('READY'));
  const ready = vi.fn();
  const { result } = renderHook(() => useDatasetImport(ready));
  await waitFor(() => expect(result.current.policy).not.toBeNull());
  await act(() => result.current.choose([imageFile()]));
  act(() => result.current.start());
  act(() => PreparationWorker.current?.complete());
  await waitFor(() => expect(result.current.operation?.state).toBe('READY'));
  expect(result.current.open).toBe(false);
  expect(result.current.files).toEqual([]);
  expect(result.current.feedback?.title).toBe('Dataset registered');
  expect(ready).toHaveBeenCalledExactlyOnceWith(dataset);
  expect(sessionStorage.getItem('tachyron.import.v1')).toBe(id);
});
