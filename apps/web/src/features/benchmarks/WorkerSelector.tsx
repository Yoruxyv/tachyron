import { workers, type WorkerCount } from '@/api/contracts';

export default function WorkerSelector({
  value,
  onChange,
}: {
  readonly value: WorkerCount;
  readonly onChange: (count: WorkerCount) => void;
}) {
  return (
    <fieldset className="worker-selector">
      <legend>Inspect worker count</legend>
      <div>
        {workers.map((count) => (
          <button
            key={count}
            type="button"
            aria-pressed={value === count}
            onClick={() => onChange(count)}
          >
            {count}
            <span className="sr-only"> workers</span>
          </button>
        ))}
      </div>
    </fieldset>
  );
}
