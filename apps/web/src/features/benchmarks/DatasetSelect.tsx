import type { Dataset } from '@/api/contracts';
import Select from '@/components/Select';

export default function DatasetSelect({
  datasets,
  value,
  onChange,
  id,
  label,
  disabled = false,
  header = false,
}: {
  readonly datasets: readonly Dataset[];
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly id?: string;
  readonly label?: string;
  readonly disabled?: boolean;
  readonly header?: boolean;
}) {
  return (
    <Select
      id={id}
      label={label ?? 'Dataset'}
      value={value}
      disabled={disabled || datasets.length === 0}
      onChange={onChange}
      header={header}
      placeholder="Choose a registered dataset"
      options={datasets.map((dataset) => ({
        value: dataset.dataset_id,
        label:
          (header
            ? dataset.dataset_id
            : dataset.dataset_id +
              ' · ' +
              (dataset.input_count ?? 'unavailable') +
              ' images') + (dataset.error_code ? ' · unavailable' : ''),
        disabled: dataset.error_code !== null || !dataset.input_count,
      }))}
    />
  );
}
