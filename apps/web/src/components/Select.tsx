import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';

export interface SelectOption {
  readonly value: string;
  readonly label: string;
  readonly disabled?: boolean;
}

/** A controlled, select-only combobox; keyboard focus remains on its trigger. */
export default function Select({
  options,
  value,
  onChange,
  label,
  id,
  disabled = false,
  header = false,
  placeholder = 'Choose an option',
}: {
  readonly options: readonly SelectOption[];
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly label: string;
  readonly id?: string | undefined;
  readonly disabled?: boolean;
  readonly header?: boolean;
  readonly placeholder?: string;
}) {
  const generated = useId();
  const triggerId = id ?? generated;
  const listId = triggerId + '-listbox';
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const [placement, setPlacement] = useState({
    above: false,
    end: false,
    maxHeight: 260,
  });
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLUListElement>(null);
  const search = useRef('');
  const selected = options.find((option) => option.value === value);
  const enabled = options.flatMap((option, index) =>
    option.disabled ? [] : [index],
  );

  function show(index?: number) {
    if (disabled) return;
    search.current = '';
    const selectedIndex = options.findIndex(
      (option) => option.value === value && !option.disabled,
    );
    setHighlight(
      index ?? (selectedIndex >= 0 ? selectedIndex : (enabled[0] ?? -1)),
    );
    setOpen(true);
  }
  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  }
  function move(direction: number) {
    if (!open) {
      show();
      return;
    }
    const index = enabled.indexOf(highlight);
    setHighlight(
      enabled[(index + direction + enabled.length) % enabled.length] ?? -1,
    );
    search.current = '';
  }
  function typeAhead(key: string) {
    const query = (search.current + key).toLocaleLowerCase();
    const ordered = [
      ...enabled.filter((index) => index > highlight),
      ...enabled.filter((index) => index <= highlight),
    ];
    let next = ordered.find((index) =>
      options[index]?.label.toLocaleLowerCase().startsWith(query),
    );
    search.current = query;
    if (next === undefined) {
      search.current = key.toLocaleLowerCase();
      next = ordered.find((index) =>
        options[index]?.label.toLocaleLowerCase().startsWith(search.current),
      );
    }
    if (next !== undefined) {
      setHighlight(next);
      setOpen(true);
    }
  }
  function keyDown(event: KeyboardEvent<HTMLButtonElement>) {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        move(1);
        return;
      case 'ArrowUp':
        event.preventDefault();
        move(-1);
        return;
      case 'Home':
        event.preventDefault();
        show(enabled[0]);
        return;
      case 'End':
        event.preventDefault();
        show(enabled.at(-1));
        return;
      case 'Enter':
      case ' ':
        event.preventDefault();
        if (open) choose(highlight);
        else show();
        return;
      case 'Escape':
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        return;
      case 'Tab':
        setOpen(false);
        return;
      default:
        if (
          event.key.length === 1 &&
          !event.ctrlKey &&
          !event.metaKey &&
          !event.altKey
        ) {
          event.preventDefault();
          typeAhead(event.key);
        }
    }
  }
  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (
        event.target instanceof Node &&
        !wrapper.current?.contains(event.target)
      )
        setOpen(false);
    }
    function scrolled(event: Event) {
      if (
        event.target instanceof Node &&
        !popup.current?.contains(event.target)
      )
        setOpen(false);
    }
    const close = () => setOpen(false);
    document.addEventListener('pointerdown', outside);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', scrolled, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', scrolled, true);
    };
  }, [open]);
  useLayoutEffect(() => {
    if (disabled) {
      setOpen(false);
      return;
    }
    if (!open || !trigger.current || !popup.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const below = innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const expected = Math.min(
      popup.current.scrollHeight,
      260,
      innerHeight * 0.42,
    );
    const upwards = below < expected && above > below;
    setPlacement({
      above: upwards,
      end:
        rect.left + popup.current.getBoundingClientRect().width >
        innerWidth - 12,
      maxHeight: Math.max(0, upwards ? above : below),
    });
  }, [open, disabled, options.length]);
  useEffect(() => {
    if (!open || !popup.current) return;
    const option = document.getElementById(listId + '-option-' + highlight);
    if (!option) return;
    const top = option.offsetTop;
    const bottom = top + option.offsetHeight;
    if (top < popup.current.scrollTop) popup.current.scrollTop = top;
    else if (bottom > popup.current.scrollTop + popup.current.clientHeight)
      popup.current.scrollTop = bottom - popup.current.clientHeight;
  }, [open, highlight, listId]);
  return (
    <div className={header ? 'select select--header' : 'select'} ref={wrapper}>
      {/* eslint-disable-next-line jsx-a11y/prefer-tag-over-role -- APG select-only combobox keeps keyboard focus on this button; a native popup cannot match the approved reference. */}
      <button
        ref={trigger}
        id={triggerId}
        className="select-trigger"
        type="button"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={
          open && highlight >= 0 ? listId + '-option-' + highlight : undefined
        }
        disabled={disabled}
        value={value}
        title={selected?.label ?? (value || placeholder)}
        onClick={() => {
          if (open) setOpen(false);
          else show();
        }}
        onKeyDown={keyDown}
      >
        <span className="select-value">
          {selected?.label ?? (value || placeholder)}
        </span>
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path
            d="m5 7.5 5 5 5-5"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open && (
        // eslint-disable-next-line jsx-a11y/prefer-tag-over-role -- The combobox owns this custom listbox through aria-controls and aria-activedescendant.
        <ul
          ref={popup}
          id={listId}
          role="listbox"
          aria-label={label}
          className={
            'select-popup' +
            (placement.above ? ' above' : '') +
            (placement.end ? ' align-end' : '')
          }
          style={{
            maxHeight: Math.min(260, innerHeight * 0.42, placement.maxHeight),
          }}
        >
          {options.map((option, index) => (
            // eslint-disable-next-line jsx-a11y/prefer-tag-over-role -- Options belong to the custom listbox, with selection and highlight exposed separately.
            <li
              id={listId + '-option-' + index}
              key={option.value}
              role="option"
              aria-selected={option.value === value}
              aria-disabled={option.disabled ?? false}
              data-highlighted={index === highlight}
              tabIndex={-1}
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() => {
                if (!option.disabled) setHighlight(index);
              }}
              onClick={() => choose(index)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  choose(index);
                }
              }}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
