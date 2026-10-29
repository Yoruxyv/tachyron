import { useCallback, useEffect, useRef, useState } from 'react';

import type { Dataset } from '@/api/contracts';
import { ApiError } from '@/api/benchmarks';
import {
  cancelImport,
  getImport,
  getImportPolicy,
  importMessage,
  importTerminal,
  uploadImport,
  type ImportOperation,
  type ImportPolicy,
} from '@/api/datasetImports';

export interface ReviewFile {
  file: File;
  path: string;
  included: boolean;
  problem: string;
}
const key = 'tachyron.import.v1';
function restore(): string {
  try {
    const value = sessionStorage.getItem(key) ?? '';
    return /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/u.test(value)
      ? value
      : '';
  } catch {
    return '';
  }
}
export function useDatasetImport(onReady: (dataset: Dataset) => void) {
  const [files, setFiles] = useState<ReviewFile[]>([]);
  const [reviewing, setReviewing] = useState(false);
  const [selectionRevision, setSelectionRevision] = useState(0);
  const [name, setName] = useState('');
  const [policy, setPolicy] = useState<ImportPolicy | null>(null);
  const [operation, setOperation] = useState<ImportOperation | null>(null);
  const [identifier, setIdentifier] = useState(restore);
  const [polling, setPolling] = useState(Boolean(identifier));
  const [preparing, setPreparing] = useState(false);
  const [prepared, setPrepared] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const worker = useRef<Worker | null>(null);
  const action = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const delivered = useRef('');
  const observed = useRef<ImportOperation | null>(null);
  const ready = useRef(onReady);
  useEffect(() => {
    ready.current = onReady;
  }, [onReady]);
  const accept = useCallback((value: ImportOperation) => {
    const previous = observed.current;
    if (
      previous?.operation_id === value.operation_id &&
      (importTerminal(previous) ||
        (previous.state === 'VALIDATING' && value.state === 'RECEIVING') ||
        (previous.cancel_requested &&
          !value.cancel_requested &&
          !importTerminal(value)))
    )
      return;
    observed.current = value;
    setOperation(value);
    if (importTerminal(value)) {
      setPolling(false);
      if (
        value.state === 'READY' &&
        value.dataset &&
        delivered.current !== value.operation_id
      ) {
        delivered.current = value.operation_id;
        ready.current(value.dataset);
        setError('');
      } else if (value.error_code) setError(importMessage(value.error_code));
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    getImportPolicy(controller.signal)
      .then(setPolicy)
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            'Import policy unavailable. Retry when the local API is available.',
          );
      });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    if (!identifier || !polling) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function poll() {
      try {
        const value = await getImport(identifier, controller.signal);
        if (controller.signal.aborted) return;
        accept(value);
        failures = 0;
        if (importTerminal(value)) return;
      } catch {
        if (controller.signal.aborted) return;
        failures += 1;
        if (failures >= 5) {
          setPolling(false);
          setError(
            'Operation outcome unconfirmed. Check status before starting another import.',
          );
          return;
        }
      }
      timer = setTimeout(() => {
        void poll();
      }, 1000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [identifier, polling, revision, accept]);
  useEffect(
    () => () => {
      generation.current += 1;
      worker.current?.terminate();
      action.current?.abort();
    },
    [],
  );

  async function choose(selected: File[]) {
    if (
      !selected.length ||
      preparing ||
      uploading ||
      (identifier && (!operation || !importTerminal(operation)))
    )
      return;
    const current = ++generation.current;
    setSelectionRevision((value) => value + 1);
    setReviewing(true);
    setFiles([]);
    setError('');
    setIdentifier('');
    setPolling(false);
    setOperation(null);
    observed.current = null;
    setProgress(null);
    setPrepared(0);
    try {
      sessionStorage.removeItem(key);
    } catch {
      setError('Reload recovery is unavailable. Keep this tab open.');
    }
    try {
      if (selected.length > 10000) {
        setError('Choose a folder with at most 10,000 entries.');
        return;
      }
      const reviewed: ReviewFile[] = [];
      for (const file of selected) {
        if (current !== generation.current) return;
        reviewed.push(
          await reviewFile(file, policy?.max_file_bytes ?? 33554432),
        );
      }
      if (current !== generation.current) return;
      setName(
        selected[0]?.webkitRelativePath.split('/')[0] || 'Imported images',
      );
      reviewed.sort((a, b) => a.path.localeCompare(b.path));
      setFiles(reviewed);
    } catch {
      if (current === generation.current)
        setError('Unable to read this folder. Select it again.');
    } finally {
      if (current === generation.current) setReviewing(false);
    }
  }

  function start() {
    if (!policy || reviewing || preparing || uploading || identifier) return;
    const selected = files.filter((entry) => entry.included);
    const bytes = selected.reduce((total, entry) => total + entry.file.size, 0);
    if (
      !selected.length ||
      selected.length > policy.max_files ||
      bytes > policy.max_total_bytes ||
      selected.some((entry) => entry.problem)
    ) {
      setError('Review included files and import limits before confirming.');
      return;
    }
    const id = crypto.randomUUID();
    const current = ++generation.current;
    const controller = new AbortController();
    action.current = controller;
    setError('');
    setPreparing(true);
    setPrepared(0);
    setOperation(null);
    observed.current = null;
    const failed = () => {
      if (current !== generation.current) return;
      worker.current?.terminate();
      worker.current = null;
      setPreparing(false);
      setError('Unable to prepare files. Choose the folder again.');
    };
    let preparation: Worker;
    try {
      preparation = new Worker(
        new URL('../prepare.worker.ts', import.meta.url),
        { type: 'module' },
      );
    } catch {
      failed();
      return;
    }
    worker.current = preparation;
    preparation.onerror = failed;
    preparation.onmessage = (event: MessageEvent<unknown>) => {
      if (
        current !== generation.current ||
        !event.data ||
        typeof event.data !== 'object'
      )
        return;
      const data = event.data;
      if (
        'kind' in data &&
        data.kind === 'progress' &&
        'count' in data &&
        typeof data.count === 'number'
      ) {
        setPrepared(data.count);
        return;
      }
      preparation.terminate();
      worker.current = null;
      setPreparing(false);
      if (
        !('metadata' in data) ||
        typeof data.metadata !== 'string' ||
        !('digest' in data) ||
        typeof data.digest !== 'string'
      ) {
        setError('Unable to prepare files.');
        return;
      }
      if (new TextEncoder().encode(data.metadata).length > 524288) {
        setError('Selection metadata is too large. Choose a smaller folder.');
        return;
      }
      const body = new FormData();
      body.append('metadata', data.metadata);
      selected.forEach((entry, index) =>
        body.append(`file-${index}`, entry.file, 'image'),
      );
      try {
        sessionStorage.setItem(key, id);
      } catch {
        setError('Reload recovery is unavailable. Keep this tab open.');
      }
      setIdentifier(id);
      setPolling(true);
      setUploading(true);
      void uploadImport(
        body,
        id,
        data.digest,
        (policy.receive_seconds +
          policy.image_seconds * (selected.length + 1) +
          30) *
          1000,
        controller.signal,
        setProgress,
      )
        .then((value) => {
          if (current === generation.current) accept(value);
        })
        .catch((failure: unknown) =>
          uploadFailed(failure, id, current, controller.signal),
        )
        .finally(() => {
          if (current === generation.current) setUploading(false);
        });
    };
    try {
      preparation.postMessage({
        files: selected.map((entry) => entry.file),
        paths: selected.map((entry) => entry.path),
        displayName: name,
        excluded: files.length - selected.length,
      });
    } catch {
      failed();
    }
  }
  async function uploadFailed(
    failure: unknown,
    id: string,
    current: number,
    signal: AbortSignal,
  ) {
    if (
      current !== generation.current ||
      signal.aborted ||
      (observed.current && importTerminal(observed.current))
    )
      return;
    setError('Upload response unconfirmed. Check operation status.');
    if (!(failure instanceof ApiError) || failure.status === 0) return;
    try {
      const value = await getImport(id, signal);
      if (current === generation.current && !signal.aborted) accept(value);
    } catch (error) {
      if (
        current !== generation.current ||
        signal.aborted ||
        !(error instanceof ApiError) ||
        error.status !== 404 ||
        ![
          'import_busy',
          'workload_busy',
          'catalog_capacity',
          'dataset_id_conflict',
          'coordinator_unavailable',
          'request_forbidden',
          'invalid_host',
          'invalid_request',
          'invalid_multipart',
        ].includes(failure.code)
      )
        return;
      // An HTTP rejection has completed; a missing receipt proves no later publication.
      setIdentifier('');
      setOperation(null);
      observed.current = null;
      setPolling(false);
      setError(failure.message);
      try {
        sessionStorage.removeItem(key);
      } catch {
        setError('Import rejected. Reload recovery storage is unavailable.');
      }
    }
  }
  async function cancel() {
    if (reviewing || preparing) {
      generation.current += 1;
      worker.current?.terminate();
      worker.current = null;
      setPreparing(false);
      setReviewing(false);
      return;
    }
    action.current?.abort();
    setUploading(false);
    if (!identifier) return;
    const current = generation.current;
    try {
      const value = await cancelImport(
        identifier,
        new AbortController().signal,
      );
      if (current !== generation.current) return;
      accept(value);
      if (!observed.current || !importTerminal(observed.current))
        setPolling(true);
    } catch {
      if (current === generation.current)
        setError('Cancellation unconfirmed. Check operation status.');
    }
  }
  return {
    files,
    reviewing,
    selectionRevision,
    name,
    setName,
    policy,
    operation,
    preparing,
    prepared,
    uploading,
    progress,
    error,
    polling,
    identifier,
    busy:
      reviewing ||
      preparing ||
      uploading ||
      Boolean(identifier && (!operation || !importTerminal(operation))),
    choose,
    start,
    cancel,
    toggle: (index: number) =>
      setFiles((items) =>
        items.map((item, i) =>
          i === index ? { ...item, included: !item.included } : item,
        ),
      ),
    check: () => {
      setPolling(Boolean(identifier));
      setRevision((value) => value + 1);
    },
  };
}

async function reviewFile(file: File, maxBytes: number): Promise<ReviewFile> {
  const path = file.webkitRelativePath
    ? file.webkitRelativePath.split('/').slice(1).join('/')
    : file.name;
  let problem = '';
  if (!file.size || file.size > maxBytes)
    problem = 'File size exceeds import limits';
  else {
    try {
      const bytes = new Uint8Array(await file.slice(0, 8).arrayBuffer());
      const png =
        bytes[0] === 137 &&
        bytes[1] === 80 &&
        bytes[2] === 78 &&
        bytes[3] === 71;
      const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
      if (!png && !jpeg)
        problem =
          'Unsupported signature; explicitly exclude or choose another folder';
    } catch {
      problem =
        'File cannot be read; explicitly exclude or choose another folder';
    }
  }
  return { file, path, included: true, problem };
}
