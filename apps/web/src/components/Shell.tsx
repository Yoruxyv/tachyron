import { useEffect, useRef, useState, type ReactNode } from 'react';

import type { Dataset } from '@/api/contracts';
import DatasetSelect from '@/features/benchmarks/DatasetSelect';

import Icon from './Icon';
import Select from './Select';
import { views, type View } from './useNavigation';

interface Props {
  readonly view: View;
  readonly navigate: (view: View) => void;
  readonly active: boolean;
  readonly children: ReactNode;
  readonly datasets: Dataset[];
  readonly datasetId: string;
  readonly selectDataset: (id: string) => void;
  readonly configurationLocked: boolean;
  readonly workspaceStatus: ReactNode;
}
export default function Shell({
  view,
  navigate,
  active,
  children,
  datasets,
  datasetId,
  selectDataset,
  configurationLocked,
  workspaceStatus,
}: Props) {
  const main = useRef<HTMLElement>(null);
  const previous = useRef(view);
  const menuButton = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (previous.current !== view) {
      main.current?.focus({ preventScroll: true });
      window.scrollTo(0, 0);
      previous.current = view;
    }
    setExpanded(false);
  }, [view]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (document.querySelector('dialog[open]')) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        navigate('history');
      }
      if (event.key === 'Escape' && expanded) {
        setExpanded(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [navigate, expanded]);
  const meta = views.find((item) => item.id === view);
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          main.current?.focus();
        }}
      >
        Skip to content
      </a>
      <aside className="sidebar">
        <a
          className="brand-block"
          href="#overview"
          aria-label="Tachyron overview"
        >
          <svg
            className="brand-emblem"
            viewBox="0 0 38 38"
            fill="none"
            aria-hidden="true"
          >
            <rect
              x="1"
              y="1"
              width="36"
              height="36"
              rx="9"
              fill="#252a31"
              stroke="#333a43"
            />
            <path
              d="M9 12H29M17 12V26M10 26V21M24 20V26M28 17V26"
              stroke="#f1f3f5"
              strokeWidth="2.1"
              strokeLinecap="square"
            />
          </svg>
          <span className="brand-copy">
            <strong>
              tachyron<span className="brand-period">.</span>
            </strong>
            <small>PERFORMANCE LAB</small>
          </span>
        </a>
        <button
          ref={menuButton}
          className="mobile-menu"
          type="button"
          aria-controls="dashboard-nav"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          Navigation {expanded ? '−' : '+'}
        </button>
        <nav
          id="dashboard-nav"
          aria-label="Dashboard sections"
          className={expanded ? 'navigation expanded' : 'navigation'}
        >
          <p className="sidebar-label">Navigation</p>
          {views.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              aria-current={view === item.id ? 'page' : undefined}
            >
              <Icon name={item.id} />
              <span>{item.label}</span>
              {item.id === 'experiments' && active && (
                <span className="activity-dot" aria-label="Active experiment" />
              )}
            </a>
          ))}
        </nav>
        {workspaceStatus}
      </aside>
      <div className="main-wrapper">
        <header className="top-header">
          <div className="header-title">
            <h1>{meta?.title}</h1>
            <p>{meta?.subtitle}</p>
          </div>
          <div className="header-actions">
            <button
              className="search-button"
              type="button"
              aria-label="Search benchmark history"
              onClick={() => navigate('history')}
            >
              <svg
                viewBox="0 0 24 24"
                width="15"
                height="15"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <circle cx="10.5" cy="10.5" r="7.5" />
                <path d="m16 16 5 5" />
              </svg>
              <span>Search runs...</span>
              <kbd>Ctrl K</kbd>
            </button>
            <div className="header-selects">
              <DatasetSelect
                datasets={datasets}
                value={datasetId}
                onChange={selectDataset}
                label="Select benchmark dataset"
                disabled={configurationLocked}
                header
              />
              <span className="pill-separator" aria-hidden="true" />
              <Select
                label="Select processing operation"
                value="resize-grayscale-v1"
                onChange={() => undefined}
                header
                options={[
                  { value: 'resize-grayscale-v1', label: 'Resize + Gray' },
                ]}
              />
            </div>
            <button
              type="button"
              className="primary"
              aria-label="New experiment"
              onClick={() => navigate('experiments')}
            >
              <svg
                viewBox="0 0 24 24"
                width="15"
                height="15"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <path d="M12 4v16M4 12h16" />
              </svg>
              <span>New experiment</span>
            </button>
          </div>
        </header>
        <main id="main-content" ref={main} tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
