import { useEffect, useState } from 'react';

import { isBenchmarkId } from '@/api/decode';

export type View = 'overview' | 'experiments' | 'compare' | 'history' | 'api';
export const views: {
  id: View;
  title: string;
  subtitle: string;
  label: string;
}[] = [
  {
    id: 'overview',
    title: 'Overview',
    subtitle: 'How much faster do images process with more workers?',
    label: 'Overview',
  },
  {
    id: 'experiments',
    title: 'Experiments',
    subtitle: 'Configure a benchmark or process a dataset',
    label: 'Experiments',
  },
  {
    id: 'compare',
    title: 'Scaling comparison',
    subtitle: 'Measured worker efficiency and speedup',
    label: 'Compare',
  },
  {
    id: 'history',
    title: 'Benchmark history',
    subtitle: 'Persisted experiments and their measured evidence',
    label: 'History',
  },
  {
    id: 'api',
    title: 'API tests',
    subtitle: 'Connection diagnostics for your local benchmark server',
    label: 'API Tests',
  },
];

export function comparisonHref(id: string, drilldown = false) {
  const path = drilldown ? 'compare/workers' : 'compare';
  return `#${path}?experiment=${encodeURIComponent(id)}`;
}

export function parseNavigation(hash: string) {
  const [path, query] = hash.slice(1).split('?', 2);
  const workerResults = path === 'compare/workers';
  const view = workerResults
    ? 'compare'
    : (views.find((entry) => entry.id === path)?.id ?? 'overview');
  const ids = new URLSearchParams(query).getAll('experiment');
  const id = ids[0];
  const comparisonId =
    view === 'compare' && ids.length === 1 && id && isBenchmarkId(id)
      ? id.toLowerCase()
      : null;
  return { view, workerResults, comparisonId };
}

const currentRoute = () => parseNavigation(window.location.hash);
/** Keeps hash navigation in sync with browser history and releases its listener. */
export function useNavigation() {
  const [route, setRoute] = useState(currentRoute);
  useEffect(() => {
    const changed = () => setRoute(currentRoute());
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  function navigate(next: View) {
    window.location.hash = next;
    setRoute(currentRoute());
  }
  return { ...route, navigate };
}
