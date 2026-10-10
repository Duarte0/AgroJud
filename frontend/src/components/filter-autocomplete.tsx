import { useEffect, useId, useState, type KeyboardEvent } from "react";

import type { ProcessFilterField, ProcessFilterOption } from "@/api/types";
import { useProcessFilterOptions } from "@/api/queries";
import { Input } from "@/components/ui/input";
import { formatCount } from "@/lib/format";

type FilterAutocompleteProps = {
  id: string;
  name: ProcessFilterField;
  value: string;
  selectedValue?: string;
  placeholder: string;
  maxLength: number;
  onChange: (value: string) => void;
  onSelect: (option: ProcessFilterOption) => void;
};

export function FilterAutocomplete({
  id,
  name,
  value,
  selectedValue,
  placeholder,
  maxLength,
  onChange,
  onSelect,
}: FilterAutocompleteProps) {
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [debouncedTerm, setDebouncedTerm] = useState(value);
  const optionsQuery = useProcessFilterOptions(
    name,
    debouncedTerm,
    selectedValue,
    open || Boolean(selectedValue),
  );
  const waitingForSearch = debouncedTerm !== value || optionsQuery.isFetching;
  const options = waitingForSearch ? [] : optionsQuery.data?.items ?? [];

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedTerm(value), 180);
    return () => window.clearTimeout(timer);
  }, [value]);

  function updateValue(next: string) {
    setActiveIndex(-1);
    setOpen(true);
    onChange(next);
  }

  function choose(option: ProcessFilterOption) {
    onSelect(option);
    setDebouncedTerm(option.label);
    setActiveIndex(-1);
    setOpen(false);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex(index => Math.min(index + 1, Math.max(options.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex(index => index < 0 ? Math.max(options.length - 1, 0) : Math.max(index - 1, 0));
    } else if (event.key === "Enter" && open && options[activeIndex]) {
      event.preventDefault();
      choose(options[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  const showListbox = open && (options.length > 0 || waitingForSearch || Boolean(debouncedTerm));
  return (
    <div className="relative">
      <Input
        id={id}
        name={name}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showListbox}
        aria-controls={listboxId}
        aria-activedescendant={showListbox && options[activeIndex] ? `${listboxId}-option-${activeIndex}` : undefined}
        autoComplete="off"
        spellCheck={false}
        maxLength={maxLength}
        value={value}
        placeholder={placeholder}
        onChange={event => updateValue(event.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={handleKeyDown}
      />
      {showListbox ? (
        <div
          id={listboxId}
          role="listbox"
          aria-label={`Sugestões para ${name}`}
          aria-busy={waitingForSearch}
          className="absolute z-30 mt-1 max-h-64 w-full overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
        >
          {options.map((option, index) => (
            <button
              key={option.value}
              id={`${listboxId}-option-${index}`}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              className="flex w-full flex-col items-start gap-0.5 rounded-sm px-3 py-2 text-left text-sm outline-none hover:bg-accent focus:bg-accent aria-selected:bg-accent"
              onPointerDown={event => event.preventDefault()}
              onClick={() => choose(option)}
            >
              <span className="w-full truncate">{option.label}</span>
              {option.detail ? (
                <span className="text-xs text-muted-foreground">{option.detail}</span>
              ) : option.process_count !== null && option.process_count !== undefined ? (
                <span className="text-xs text-muted-foreground">
                  {formatCount(option.process_count)} {option.process_count === 1 ? "processo" : "processos"}
                </span>
              ) : null}
            </button>
          ))}
          {waitingForSearch && options.length === 0 ? (
            <p role="status" className="px-3 py-2 text-sm text-muted-foreground">Buscando opções…</p>
          ) : null}
          {!waitingForSearch && options.length === 0 && debouncedTerm ? (
            <p role="status" className="px-3 py-2 text-sm text-muted-foreground">Nenhuma opção correspondente. Você ainda pode usar o texto digitado.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
