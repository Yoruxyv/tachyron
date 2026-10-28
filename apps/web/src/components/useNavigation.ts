import { useEffect, useState } from 'react';

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
    subtitle: 'Configure a workload and run a worker benchmark',
    label: 'Experiments',
  },
  {
    id: 'compare',
    title: 'Scaling comparison',
    subtitle: '1, 2, 4, and 8 worker efficiency & speedup analysis',
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

function currentView(): View {
  return (
    views.find((view) => `#${view.id}` === window.location.hash)?.id ??
    'overview'
  );
}
/** Keeps hash navigation in sync with browser history and releases its listener. */
export function useNavigation() {
  const [view, setView] = useState(currentView);
  useEffect(() => {
    const changed = () => setView(currentView());
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  function navigate(next: View) {
    window.location.hash = next;
    setView(next);
  }
  return { view, navigate };
}
