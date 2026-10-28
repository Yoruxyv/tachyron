import { useEffect, useRef, useState } from 'react';

import { ApiError, getDatasets, getDetail, getHistory } from '@/api/benchmarks';
import {
  type Dataset,
  type HistoryDetail,
  type HistoryItem,
} from '@/api/contracts';

const failureText = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : 'Unable to load saved experiments.';
/** Fetch the frozen dataset registry, aborting obsolete requests and exposing retry state. */
export function useCatalog() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [verifiedAt, setVerifiedAt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getDatasets(controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) {
          setDatasets(items);
          setError('');
          setVerifiedAt(performance.now());
        }
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(failureText(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision]);
  return {
    datasets,
    error,
    loading,
    verifiedAt,
    reload: () => {
      setLoading(true);
      setRevision((value) => value + 1);
    },
  };
}

/**
 * Own a bounded history page and one selected experiment's evidence.
 * Obsolete responses cannot replace the current selection. A newly terminal job
 * returns the list to its first page; subsequent pagination remains user-owned.
 * @param terminalId Newly terminal local job, or null while no job has finished.
 * @returns Independent list/detail loading and error states, selection, and pagination.
 */
export function useHistory(terminalId: string | null) {
  const [verifiedAt, setVerifiedAt] = useState(0);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [settledOffset, setSettledOffset] = useState<number | null>(null);
  const previousOffset = useRef(offset);
  const previousSelection = useRef(selectedId);

  useEffect(() => {
    if (terminalId) {
      setOffset(0);
      setSelectedId(terminalId);
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
        setSelectedId((current) =>
          records.some((record) => record.job.id === current)
            ? current
            : (records[0]?.job.id ?? ''),
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
  }, [offset, revision, terminalId]);
  useEffect(() => {
    if (previousSelection.current !== selectedId) {
      setDetail(null);
      setDetailError('');
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
          setDetailError('');
          setVerifiedAt(performance.now());
        }
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setDetailError(failureText(failure));
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
    select: setSelectedId,
    detail,
    error,
    detailError,
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
