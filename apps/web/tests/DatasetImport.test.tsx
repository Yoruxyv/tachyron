import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/api/datasetImports';
import { ApiError } from '@/api/benchmarks';
import type { ImportOperation } from '@/api/datasetImports';
import DatasetFolderPicker from '@/features/datasets/components/DatasetFolderPicker';
import DatasetImport from '@/features/datasets/components/DatasetImport';
import { useDatasetImport } from '@/features/datasets/hooks/useDatasetImport';
import { dataset } from './fixtures';
import { imageFile } from './importFixtures';

const guidance = 'PNG, JPG, JPEG only. Files must be valid and uncorrupted.';
const identifier = '12345678-1234-4234-8234-123456789abc';
const ready = vi.fn();
function Harness({ locked = false }: { readonly locked?: boolean }) {
  const importer = useDatasetImport(ready);
  return (
    <>
      <DatasetFolderPicker
        choose={importer.choose}
        disabled={locked || importer.busy}
      />
      <select aria-label="Dataset">
        <option>Existing images</option>
      </select>
      {importer.open && <DatasetImport importer={importer} disabled={locked} />}
    </>
  );
}
function select(files: File[]) {
  fireEvent.change(screen.getByLabelText('Choose image folder'), {
    target: { files },
  });
}
function operation(state: ImportOperation['state']): ImportOperation {
  return {
    operation_id: identifier,
    state,
    cancel_requested: false,
    dataset: state === 'READY' ? dataset : null,
    error_code: null,
    progress: {
      total_files: 1,
      received_files: 1,
      validated_files: state === 'READY' ? 1 : 0,
    },
  };
}
beforeEach(() => {
  sessionStorage.clear();
  ready.mockReset();
  vi.spyOn(api, 'getImportPolicy').mockResolvedValue({
    max_files: 1000,
    max_file_bytes: 33554432,
    max_total_bytes: 536870912,
    receive_seconds: 600,
    image_seconds: 60,
  });
  vi.spyOn(api, 'uploadImport');
});
describe('native folder import workflow', () => {
  it('opens the directory input directly by keyboard and picker cancellation opens no modal', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const picker = screen.getByLabelText('Choose image folder');
    const open = vi.spyOn(picker, 'click');
    const button = screen.getByRole('button', { name: 'Import folder' });
    expect(button).toHaveAccessibleDescription(guidance);
    expect(picker).toHaveAttribute('webkitdirectory');
    expect(picker).not.toHaveAttribute('accept');
    await user.tab();
    await user.keyboard('{Enter}');
    expect(open).toHaveBeenCalledTimes(1);
    fireEvent(picker, new Event('cancel'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('allows review of six PNG/JPEG images without trusting MIME and never automatically uploads', async () => {
    render(<Harness />);
    select([
      imageFile('1.PNG'),
      imageFile('2.png'),
      imageFile('3.png'),
      imageFile('4.jpg', [255, 216, 255]),
      imageFile('5.jpeg', [255, 216, 255]),
      imageFile('6.JPG', [255, 216, 255]),
    ]);
    const dialog = await screen.findByRole('dialog', {
      name: 'Review dataset folder',
    });
    expect(
      await within(dialog).findByText(/6 included .*0 explicitly excluded/),
    ).toBeVisible();
    expect(
      within(dialog).getByRole('button', {
        name: 'Confirm and import 6 files',
      }),
    ).toBeEnabled();
    expect(
      within(dialog).getByRole('checkbox', { name: /I confirm/ }),
    ).toHaveAccessibleName(
      'I confirm 6 included files and 0 exclusions for this benchmark workload.',
    );
    expect(
      within(dialog).getByRole('heading', { name: 'Review dataset folder' }),
    ).toHaveFocus();
    expect(within(dialog).getByRole('status')).toHaveTextContent(
      'Review 6 included files and confirm exclusions before uploading.',
    );
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('blocks five images plus a PDF with no exclusion or confirm bypass and preserves the dataset', async () => {
    render(<Harness />);
    select([
      ...Array.from({ length: 5 }, (_, i) => imageFile(`${i}.png`)),
      imageFile('report.pdf', [37, 80, 68, 70]),
    ]);
    expect(await screen.findByText('Unsupported files detected')).toBeVisible();
    expect(screen.getByText('nested/report.pdf')).toBeVisible();
    expect(screen.getByText(/PDF document/)).toBeVisible();
    expect(screen.getByText('1 unsupported file found:')).toBeVisible();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Confirm and import/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue(
      'Existing images',
    );
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it.each([
    'report.jpg',
    '.DS_Store',
    'Thumbs.db',
    'notes.txt',
    'archive.zip',
    'movie.mp4',
    'program.exe',
    'image.svg',
    'image.png',
  ])('rejects unsupported content or names (%s)', async (name) => {
    render(<Harness />);
    select([imageFile(name, [37, 80, 68, 70])]);
    expect(await screen.findByText('Unsupported files detected')).toBeVisible();
    expect(screen.getByText(`nested/${name}`)).toBeVisible();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('bounds unsupported lists to 50 rendered entries and identifies the rest through pagination', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    select(
      Array.from({ length: 61 }, (_, i) =>
        imageFile(`sidecar-${String(i).padStart(2, '0')}.txt`),
      ),
    );
    expect(
      await screen.findByText('61 unsupported files found:'),
    ).toBeVisible();
    const list = screen.getByRole('region', {
      name: 'Unsupported folder files',
    });
    expect(within(list).getAllByRole('listitem')).toHaveLength(50);
    await user.click(screen.getByRole('button', { name: 'Next files' }));
    expect(within(list).getAllByRole('listitem')).toHaveLength(11);
    expect(screen.getByText('nested/sidecar-60.txt')).toBeVisible();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('chooses another folder through the same native input, clearing rejection and consent without uploading', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const picker = screen.getByLabelText('Choose image folder');
    const open = vi.spyOn(picker, 'click');
    select([imageFile('report.pdf')]);
    await screen.findByText('Unsupported files detected');
    await user.click(
      screen.getByRole('button', { name: 'Choose another folder' }),
    );
    expect(open).toHaveBeenCalledTimes(1);
    const file = imageFile();
    select([file]);
    const included = await screen.findByRole('checkbox', {
      name: /nested\/image.png/,
    });
    const confirmation = screen.getByRole('checkbox', { name: /I confirm/ });
    expect(confirmation).toHaveAccessibleName(
      'I confirm 1 included file and 0 exclusions for this benchmark workload.',
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Review 1 included file and confirm exclusions before uploading.',
    );
    expect(
      screen.queryByText('Unsupported files detected'),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole('button', { name: 'Confirm and import 1 file' }),
    );
    expect(confirmation).toHaveFocus();
    expect(confirmation).toHaveAccessibleDescription(
      'Acknowledge the included images and exclusions before importing.',
    );
    await user.click(confirmation);
    await user.click(included);
    expect(confirmation).not.toBeChecked();
    expect(confirmation).toHaveAccessibleName(
      'I confirm 0 included files and 1 exclusion for this benchmark workload.',
    );
    select([file]);
    expect(
      await screen.findByRole('checkbox', { name: /nested\/image.png/ }),
    ).toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: /I confirm/ }),
    ).not.toBeChecked();
    expect(picker).toHaveValue('');
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('handles an empty unusable selection and restores focus on close', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Import folder' });
    trigger.focus();
    select([]);
    expect(
      await screen.findByText(/No usable files were selected/),
    ).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
  it.each([1, 2])(
    'reports file-limit problems with matching image wording (count: %i)',
    async (count) => {
      vi.mocked(api.getImportPolicy).mockResolvedValue({
        max_files: 1000,
        max_file_bytes: 7,
        max_total_bytes: 536870912,
        receive_seconds: 600,
        image_seconds: 60,
      });
      await act(async () => {
        render(<Harness />);
      });
      select(
        Array.from({ length: count }, (_, index) => imageFile(`${index}.png`)),
      );
      const wording =
        count === 1 ? '1 included image exceeds' : '2 included images exceed';
      expect(
        await screen.findByText(
          `${wording} a file limit. Exclude them explicitly or choose another folder.`,
        ),
      ).toBeVisible();
      expect(
        screen.getByRole('button', { name: /Confirm and import/ }),
      ).toBeDisabled();
      expect(api.uploadImport).not.toHaveBeenCalled();
    },
  );
  it('preserves recovery and explicit cancellation while Escape cannot abandon validation', async () => {
    sessionStorage.setItem('tachyron.import.v1', identifier);
    vi.spyOn(api, 'getImport').mockResolvedValue(operation('VALIDATING'));
    vi.spyOn(api, 'cancelImport').mockResolvedValue(operation('CANCELLED'));
    const user = userEvent.setup();
    render(<Harness />);
    expect(await screen.findByText('Validating 0/1 file')).toBeVisible();
    fireEvent(
      screen.getByRole('dialog'),
      new Event('cancel', { cancelable: true }),
    );
    expect(screen.getByRole('dialog')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Cancel import' }));
    expect(await screen.findByText('Import cancelled')).toBeVisible();
    expect(sessionStorage.getItem('tachyron.import.v1')).toBe(identifier);
    expect(
      screen.queryByRole('button', { name: 'Check import status' }),
    ).not.toBeInTheDocument();
  });
  it('restores READY silently without an obsolete modal or status button, and permits another import', async () => {
    sessionStorage.setItem('tachyron.import.v1', identifier);
    vi.spyOn(api, 'getImport').mockResolvedValue(operation('READY'));
    render(<Harness />);
    await waitFor(() => expect(ready).toHaveBeenCalledWith(dataset));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Check import status' }),
    ).not.toBeInTheDocument();
    expect(sessionStorage.getItem('tachyron.import.v1')).toBe(identifier);
    select([imageFile()]);
    expect(
      await screen.findByRole('button', { name: 'Confirm and import 1 file' }),
    ).toBeEnabled();
    expect(sessionStorage.getItem('tachyron.import.v1')).toBeNull();
  });
  it('blocks preparation if a benchmark starts after review', async () => {
    const { rerender } = render(<Harness />);
    select([imageFile()]);
    const confirm = await screen.findByRole('button', {
      name: 'Confirm and import 1 file',
    });
    await waitFor(() => expect(confirm).toBeEnabled());
    rerender(<Harness locked />);
    expect(confirm).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Import folder' }),
    ).toBeDisabled();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });

  it('offers recovery after interrupted polling and resolves the retained receipt without uploading again', async () => {
    vi.useFakeTimers();
    sessionStorage.setItem('tachyron.import.v1', identifier);
    const status = vi
      .spyOn(api, 'getImport')
      .mockRejectedValue(new ApiError('Offline'));
    render(<Harness />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(
      screen.getByText(
        'Operation outcome unconfirmed. Check status before starting another import.',
      ),
    ).toBeVisible();
    const retry = screen.getByRole('button', { name: 'Check import status' });
    expect(
      screen.getByRole('button', { name: 'Import folder' }),
    ).toBeDisabled();
    status.mockResolvedValue(operation('READY'));
    fireEvent.click(retry);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(sessionStorage.getItem('tachyron.import.v1')).toBe(identifier);
    expect(ready).toHaveBeenCalledExactlyOnceWith(dataset);
    expect(api.uploadImport).not.toHaveBeenCalled();
  });

  it('renders untrusted paths as text and rejects unreadable files without an upload', async () => {
    render(<Harness />);
    const file = imageFile('unreadable.png');
    const blob = file.slice();
    Object.defineProperty(blob, 'arrayBuffer', {
      value: () => Promise.reject(new Error('private path')),
    });
    vi.mocked(file.slice).mockReturnValue(blob);
    select([file, imageFile('<img src=x onerror=alert(1)>.txt')]);
    expect(await screen.findByText('2 unsupported files found:')).toBeVisible();
    expect(screen.getByText(/File cannot be read/)).toBeVisible();
    expect(
      screen.getByText('nested/<img src=x onerror=alert(1)>.txt'),
    ).toBeVisible();
    expect(document.querySelector('img')).toBeNull();
    expect(screen.queryByText('private path')).not.toBeInTheDocument();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });

  it('separates preparation, transfer, validation and confirmed cancellation without abandoning the modal', async () => {
    class WorkerFixture {
      static readonly instances: WorkerFixture[] = [];
      onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
      onerror: (() => void) | null = null;
      postMessage = vi.fn();
      terminate = vi.fn();
      constructor() {
        WorkerFixture.instances.push(this);
      }
    }
    vi.stubGlobal('Worker', WorkerFixture);
    let updateProgress: ((value: number | null) => void) | undefined;
    let status: ((value: ImportOperation) => void) | undefined;
    let cancelled: ((value: ImportOperation) => void) | undefined;
    vi.spyOn(api, 'getImport').mockImplementation(
      () =>
        new Promise((resolve) => {
          status = resolve;
        }),
    );
    vi.mocked(api.uploadImport).mockImplementation(
      (_body, _id, _digest, _timeout, signal, progress) =>
        new Promise((_resolve, reject) => {
          updateProgress = progress;
          signal.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          );
        }),
    );
    vi.spyOn(api, 'cancelImport').mockImplementation(
      () =>
        new Promise((resolve) => {
          cancelled = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<Harness />);
    select([imageFile()]);
    await user.click(
      await screen.findByRole('checkbox', { name: /I confirm/ }),
    );
    await user.click(
      screen.getByRole('button', { name: 'Confirm and import 1 file' }),
    );
    expect(screen.getByText('Preparing originals: 0/1')).toBeVisible();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    act(() =>
      WorkerFixture.instances[0]?.onmessage?.(
        new MessageEvent('message', {
          data: { kind: 'complete', metadata: '{}', digest: 'a'.repeat(64) },
        }),
      ),
    );
    expect(
      screen.getByText('Transferring files; registration is pending.'),
    ).toBeVisible();
    act(() => updateProgress?.(0.4));
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0.4');
    await act(async () => {
      status?.(operation('VALIDATING'));
    });
    expect(screen.getByText('Validating 0/1 file')).toBeVisible();
    const dialog = screen.getByRole('dialog');
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(dialog).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Cancel import' }));
    expect(screen.getByRole('button', { name: 'Cancelling…' })).toBeDisabled();
    expect(api.cancelImport).toHaveBeenCalledTimes(1);
    await act(async () => {
      cancelled?.(operation('CANCELLED'));
    });
    expect(screen.getByText('Import cancelled')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Check import status' }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.uploadImport).toHaveBeenCalledTimes(1);
    expect(ready).not.toHaveBeenCalled();
  });
});
