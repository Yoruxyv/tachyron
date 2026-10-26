import { useEffect, useRef, useState } from 'react';

import { getHealth } from './api/health';

type Connection = 'idle' | 'checking' | 'online' | 'unavailable';

const connectionText: Record<Connection, string> = {
  idle: 'Not checked',
  checking: 'Checking API connection…',
  online: 'API online',
  unavailable:
    'API unavailable. Check that the local server is running, then retry.',
};

export default function App() {
  const [connection, setConnection] = useState<Connection>('idle');
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  async function checkConnection(): Promise<void> {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setConnection('checking');
    try {
      await getHealth(controller.signal);
      if (!controller.signal.aborted) setConnection('online');
    } catch {
      if (!controller.signal.aborted) setConnection('unavailable');
    } finally {
      request.current = null;
    }
  }

  return (
    <main className="mx-auto flex min-h-svh max-w-3xl flex-col justify-center gap-10 px-6 py-16">
      <header>
        <p className="mb-3 font-mono text-xs tracking-widest text-neutral-400 uppercase">
          Performance engineering
        </p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Tachyron
        </h1>
        <p className="mt-4 max-w-xl text-lg text-neutral-300">
          Reproducible image-processing benchmarks, on your machine.
        </p>
      </header>

      <section
        aria-labelledby="runs-title"
        className="rounded-lg border border-neutral-800 p-6"
      >
        <h2 id="runs-title" className="text-xl font-medium">
          No benchmark runs yet
        </h2>
        <p className="mt-3 leading-relaxed text-neutral-400">
          Sequential image processing is available through the Python CLI.
          Benchmark execution is not available yet.
        </p>
      </section>

      <section aria-labelledby="connection-title">
        <h2 id="connection-title" className="font-medium">
          API connection
        </h2>
        <output className="mt-2 block text-sm text-neutral-400">
          {connectionText[connection]}
        </output>
        <button
          type="button"
          disabled={connection === 'checking'}
          onClick={checkConnection}
          className="mt-4 cursor-pointer rounded-md border border-neutral-700 px-4 py-2 text-sm font-medium hover:bg-neutral-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white disabled:cursor-wait disabled:opacity-60"
        >
          {connection === 'checking' ? 'Checking…' : 'Check connection'}
        </button>
      </section>
    </main>
  );
}
