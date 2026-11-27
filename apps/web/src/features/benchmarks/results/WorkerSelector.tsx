import { workers } from '@/api/contracts';

export default function WorkerSelector({
  value,
  onChange,
  counts = workers,
}: {
  readonly value: number;
  readonly onChange: (count: number) => void;
  readonly counts?: readonly number[];
}) {
  return (
    <fieldset className="worker-selector">
      <legend>Inspect worker count</legend>
      <div
        style={{
          gridTemplateColumns: `repeat(${Math.min(counts.length, 5)}, minmax(0, 1fr))`,
        }}
      >
        {counts.map((count) => (
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
