import { useEffect, useState } from 'react';

import { ApiError, getDatasets } from '@/api/benchmarks';
import type { Dataset } from '@/api/contracts';

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
