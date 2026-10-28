import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';

import Select from '@/components/Select';

const options = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Beta', disabled: true },
  { value: 'c', label: 'Charlie' },
  { value: 'd', label: 'Delta' },
];

function Harness() {
  const [value, setValue] = useState('a');
  return (
    <>
      <Select
        label="Dataset"
        value={value}
        onChange={setValue}
        options={options}
      />
      <button type="button">Next control</button>
    </>
  );
}

it('navigates enabled options without changing selection until committed and restores focus', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const trigger = screen.getByRole('combobox');
  await user.tab();
  expect(trigger).toHaveFocus();
  await user.keyboard('{ArrowDown}{ArrowDown}');
  expect(screen.getByRole('option', { name: 'Charlie' }).id).toBe(
    trigger.getAttribute('aria-activedescendant'),
  );
  expect(screen.getByRole('option', { name: 'Alpha' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await user.keyboard('{Enter}');
  expect(trigger).toHaveTextContent('Charlie');
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  await user.keyboard(' {Home}{Escape}');
  expect(trigger).toHaveTextContent('Charlie');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await user.keyboard('{End} ');
  expect(trigger).toHaveTextContent('Delta');
});

it('supports typeahead, closes on Tab and outside click without stealing focus', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const trigger = screen.getByRole('combobox');
  await user.tab();
  await user.keyboard('ch{Enter}');
  expect(trigger).toHaveTextContent('Charlie');
  await user.keyboard('{ArrowUp}{Tab}');
  expect(screen.getByRole('button', { name: 'Next control' })).toHaveFocus();
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  await user.click(trigger);
  await user.click(screen.getByRole('button', { name: 'Next control' }));
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Next control' })).toHaveFocus();
});

it('renders one placeholder and rejects unavailable choices and disabled interactions', async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  const { rerender } = render(
    <Select
      label="Dataset"
      value=""
      placeholder="Choose a registered dataset"
      onChange={change}
      options={options}
    />,
  );
  expect(screen.getAllByText('Choose a registered dataset')).toHaveLength(1);
  await user.click(screen.getByRole('combobox'));
  await user.click(screen.getByRole('option', { name: 'Beta' }));
  expect(change).not.toHaveBeenCalled();
  expect(screen.getByRole('combobox')).toHaveAttribute('aria-expanded', 'true');
  rerender(
    <Select
      label="Dataset"
      value=""
      onChange={change}
      options={options}
      disabled
    />,
  );
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  await user.click(screen.getByRole('combobox'));
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(change).not.toHaveBeenCalled();
});
