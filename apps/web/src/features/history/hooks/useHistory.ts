import { useEffect, useRef, useState } from 'react';

import { ApiError, getDetail, getHistory } from '@/api/benchmarks';
import { type HistoryDetail, type HistoryItem } from '@/api/contracts';

const failureText = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : 'Unable to load saved experiments.';

/**
 * Own a bounded history page and one selected experiment's evidence.
 * Obsolete responses cannot replace the current selection. A newly terminal job
 * returns the list to its first page; subsequent pagination remains user-owned.
 * @param terminalId Newly terminal local job, or null while no job has finished.
 * @param comparisonId Saved job explicitly pinned by the current comparison link.
 * @returns Independent list/detail loading and error states, selection, and pagination.
 */
export function useHistory(
  terminalId: string | null,
  comparisonId: string | null = null,
) {
  const [verifiedAt, setVerifiedAt] = useState(0);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState(comparisonId ?? '');
  const selectedId = comparisonId ?? selection;
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [error, setError] = useState('');
  const [detailFailure, setDetailFailure] = useState<{
    id: string;
    message: string;
  } | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [settledOffset, setSettledOffset] = useState<number | null>(null);
  const previousOffset = useRef(offset);
  const previousSelection = useRef(selectedId);

  useEffect(() => {
    if (terminalId) {
      setOffset(0);
      setSelection(terminalId);
    }
  }, [terminalId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    if (previousOffset.current !== offset) {
      setItems([]);
      previousOffset.current = offset;
    }
    getHistory(controller.signal, offset)
      .then((records) => {
        if (controller.signal.aborted) return;
        setItems(records);
        setVerifiedAt(performance.now());
        setError('');
        setSelection(
          (current) =>
            comparisonId ??
            (records.some((record) => record.job.id === current)
              ? current
              : (records[0]?.job.id ?? '')),
        );
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(failureText(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setSettledOffset(offset);
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [offset, revision, terminalId, comparisonId]);
  useEffect(() => {
    if (previousSelection.current !== selectedId) {
      setDetail(null);
      setDetailFailure(null);
      previousSelection.current = selectedId;
    }
    if (!selectedId) {
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    getDetail(selectedId, controller.signal)
      .then((record) => {
        if (!controller.signal.aborted) {
          setDetail((previous) =>
            JSON.stringify(previous) === JSON.stringify(record)
              ? previous
              : record,
          );
          setDetailFailure(null);
          setVerifiedAt(performance.now());
        }
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setDetailFailure({ id: selectedId, message: failureText(failure) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selectedId, revision, terminalId]);
  return {
    items,
    offset,
    setOffset,
    selectedId,
    select: (id: string) => setSelection(id),
    detail: detail?.job.id === selectedId ? detail : null,
    error,
    detailError: detailFailure?.id === selectedId ? detailFailure.message : '',
    loading,
    initialLoading: loading && settledOffset !== offset,
    detailLoading,
    verifiedAt,
    reload: () => {
      setLoading(true);
      setDetailLoading(Boolean(selectedId));
      setRevision((value) => value + 1);
    },
  };
}
