import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/api/datasetImports';
import type { ImportOperation } from '@/api/datasetImports';
import DatasetFolderPicker from '@/features/datasets/components/DatasetFolderPicker';
import DatasetImport from '@/features/datasets/components/DatasetImport';
import { useDatasetImport } from '@/features/datasets/hooks/useDatasetImport';
import { dataset } from './fixtures';

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
      {(importer.selectionRevision > 0 || importer.identifier) && (
        <DatasetImport
          key={importer.selectionRevision}
          importer={importer}
          disabled={locked}
        />
      )}
    </>
  );
}
function image(): File {
  const bytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
  const file = new File([bytes], 'image.txt', { type: 'text/plain' });
  Object.defineProperty(file, 'webkitRelativePath', {
    value: 'images/nested/image.txt',
  });
  const slice = file.slice.bind(file);
  vi.spyOn(file, 'slice').mockImplementation((start, end, type) => {
    const blob = slice(start, end, type);
    Object.defineProperty(blob, 'arrayBuffer', {
      value: () => Promise.resolve(bytes.slice(start, end).buffer),
    });
    return blob;
  });
  return file;
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
  it('opens the directory input directly by keyboard without an intermediate panel', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const picker = screen.getByLabelText('Choose image folder');
    const open = vi.spyOn(picker, 'click');
    const button = screen.getByRole('button', { name: 'Import folder' });
    expect(button).toHaveAccessibleDescription(guidance);
    expect(picker).toHaveAttribute('webkitdirectory');
    expect(picker).not.toHaveAttribute('accept');
    expect(picker).not.toBeVisible();
    await user.tab();
    expect(button).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(open).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent(picker, new Event('cancel'));
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('reviews nested paths without uploading and resets same-folder exclusions and confirmation', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const picker = screen.getByLabelText('Choose image folder');
    const file = image();
    fireEvent.change(picker, { target: { files: [file] } });
    expect(
      await screen.findByRole('heading', { name: 'Review dataset folder' }),
    ).toBeVisible();
    const included = await screen.findByRole('checkbox', {
      name: /nested\/image.txt/,
    });
    expect(included).toBeChecked();
    const confirmation = screen.getByRole('checkbox', { name: /I confirm/ });
    await user.click(confirmation);
    expect(confirmation).toBeChecked();
    await user.click(included);
    expect(
      screen.getByText(/0 included .*1 explicitly excluded/),
    ).toBeVisible();
    expect(confirmation).not.toBeChecked();
    fireEvent.change(picker, { target: { files: [file] } });
    expect(
      await screen.findByRole('checkbox', { name: /nested\/image.txt/ }),
    ).toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: /I confirm/ }),
    ).not.toBeChecked();
    expect(document.querySelectorAll('input[type="file"]')).toHaveLength(1);
    expect(picker).toHaveValue('');
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('requires explicit unsupported-file exclusion and confirmation; extensions remain guidance', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    fireEvent.change(screen.getByLabelText('Choose image folder'), {
      target: { files: [image(), new File(['sidecar'], '.DS_Store')] },
    });
    const excluded = await screen.findByRole('checkbox', {
      name: /\.DS_Store/,
    });
    expect(excluded).toBeChecked();
    expect(
      screen.getByRole('button', { name: /Confirm and import/ }),
    ).toBeDisabled();
    await user.click(excluded);
    expect(
      screen.getByRole('button', { name: 'Confirm and import 1 files' }),
    ).toBeEnabled();
    await user.click(
      screen.getByRole('button', { name: 'Confirm and import 1 files' }),
    );
    expect(screen.getByRole('checkbox', { name: /I confirm/ })).toHaveFocus();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('preserves operation recovery and cancellation without opening a folder dialog', async () => {
    sessionStorage.setItem('tachyron.import.v1', identifier);
    vi.spyOn(api, 'getImport').mockResolvedValue(operation('VALIDATING'));
    vi.spyOn(api, 'cancelImport').mockResolvedValue(operation('CANCELLED'));
    const user = userEvent.setup();
    render(<Harness />);
    expect(await screen.findByText('Validating 0/1 files')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Import folder' }),
    ).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Cancel import' }));
    await waitFor(() =>
      expect(api.cancelImport).toHaveBeenCalledWith(
        identifier,
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByText('Import cancelled')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Import folder' })).toBeEnabled();
  });
  it('can select another folder after READY without retaining the old operation identity', async () => {
    sessionStorage.setItem('tachyron.import.v1', identifier);
    vi.spyOn(api, 'getImport').mockResolvedValue(operation('READY'));
    render(<Harness />);
    await screen.findByText('Dataset registered and available for selection.');
    expect(ready).toHaveBeenCalledWith(dataset);
    fireEvent.change(screen.getByLabelText('Choose image folder'), {
      target: { files: [image()] },
    });
    await screen.findByRole('checkbox', { name: /nested\/image.txt/ });
    expect(
      screen.getByRole('button', { name: 'Confirm and import 1 files' }),
    ).toBeEnabled();
    expect(sessionStorage.getItem('tachyron.import.v1')).toBeNull();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
  it('blocks selection during a known benchmark', () => {
    render(<Harness locked />);
    expect(
      screen.getByRole('button', { name: 'Import folder' }),
    ).toBeDisabled();
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
  });
  it('blocks import preparation if a benchmark starts after folder review', async () => {
    const { rerender } = render(<Harness />);
    fireEvent.change(screen.getByLabelText('Choose image folder'), {
      target: { files: [image()] },
    });
    const confirm = await screen.findByRole('button', {
      name: 'Confirm and import 1 files',
    });
    await waitFor(() => expect(confirm).toBeEnabled());
    rerender(<Harness locked />);
    expect(confirm).toBeDisabled();
    expect(api.uploadImport).not.toHaveBeenCalled();
  });
});
