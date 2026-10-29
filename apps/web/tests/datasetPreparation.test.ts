import { createHash, webcrypto } from 'node:crypto';
import { expect, it, vi } from 'vitest';

interface PreparationInput {
  files: File[];
  paths: string[];
  displayName: string;
  excluded: number;
}
it.each([false, true])(
  'hashes original bytes/exact UTF-8 metadata or reports unreadable files (unreadable: %s)',
  async (unreadable) => {
    const port: {
      onmessage?: (event: MessageEvent<PreparationInput>) => Promise<void>;
      postMessage: ReturnType<typeof vi.fn>;
    } = { postMessage: vi.fn() };
    vi.stubGlobal('self', port);
    vi.stubGlobal('crypto', webcrypto);
    vi.resetModules();
    await import('@/features/datasets/prepare.worker');
    const bytes = new Uint8Array([137, 80, 78, 71]);
    const file = new File([bytes], 'a.png');
    Object.defineProperty(file, 'arrayBuffer', {
      value: () =>
        unreadable
          ? Promise.reject(new Error('C:/private'))
          : Promise.resolve(bytes.buffer),
    });
    await port.onmessage?.(
      new MessageEvent('message', {
        data: {
          files: [file],
          paths: ['nested/a.png'],
          displayName: 'Cámara',
          excluded: 1,
        },
      }),
    );
    if (unreadable) {
      expect(port.postMessage).toHaveBeenCalledExactlyOnceWith({
        kind: 'error',
      });
      return;
    }
    const metadata = JSON.stringify({
      schema_version: 1,
      display_name: 'Cámara',
      selection: { mode: 'explicit-subset', excluded_count: 1 },
      files: [
        {
          part: 'file-0',
          relative_path: 'nested/a.png',
          size_bytes: file.size,
          sha256: createHash('sha256').update(bytes).digest('hex'),
        },
      ],
    });
    expect(port.postMessage).toHaveBeenNthCalledWith(1, {
      kind: 'progress',
      count: 1,
    });
    expect(port.postMessage).toHaveBeenNthCalledWith(2, {
      kind: 'complete',
      metadata,
      digest: createHash('sha256').update(metadata, 'utf8').digest('hex'),
    });
  },
);
