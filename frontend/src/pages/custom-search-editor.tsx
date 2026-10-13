import { useState, type SubmitEvent } from "react";

import { describeError } from "@/api/client";
import {
  useCreateCustomSearch,
  usePatchCustomSearch,
  useTpuCodes,
} from "@/api/queries";
import type { CustomSearch, SearchType } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type CustomSearchSeed = {
  name: string;
  description: string | null;
  classes: number[];
  subjects: number[];
  combination: "AND" | "OR";
};

function initialSeed(
  search?: CustomSearch,
  theme?: SearchType,
  seed?: CustomSearchSeed,
): CustomSearchSeed {
  if (seed) return seed;
  if (search) {
    return {
      name: search.name,
      description: search.description,
      classes: search.criteria.classes.map((item) => item.code),
      subjects: search.criteria.subjects.map((item) => item.code),
      combination: search.criteria.dimension_combination,
    };
  }
  if (theme) {
    return {
      name: `${theme.name} (personalizada)`,
      description: theme.description,
      classes: theme.criteria.classes,
      subjects: theme.criteria.subjects,
      combination: theme.combinations.between_dimensions,
    };
  }
  return { name: "", description: null, classes: [], subjects: [], combination: "AND" };
}

export function CustomSearchEditor({
  search,
  theme,
  seed,
  onClose,
  onSaved,
}: {
  search?: CustomSearch;
  theme?: SearchType;
  seed?: CustomSearchSeed;
  onClose: () => void;
  onSaved?: (search: CustomSearch) => void;
}) {
  const original = initialSeed(search, theme, seed);
  const [name, setName] = useState(original.name);
  const [description, setDescription] = useState(original.description ?? "");
  const [classes, setClasses] = useState(original.classes);
  const [subjects, setSubjects] = useState(original.subjects);
  const [combination, setCombination] = useState<"AND" | "OR">(original.combination);
  const [classQuery, setClassQuery] = useState("");
  const [subjectQuery, setSubjectQuery] = useState("");
  const codesForClasses = useTpuCodes("class", classQuery);
  const codesForSubjects = useTpuCodes("subject", subjectQuery);
  const create = useCreateCustomSearch();
  const patch = usePatchCustomSearch();
  const pending = create.isPending || patch.isPending;
  const error = create.error ?? patch.error;

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (!name.trim() || (classes.length === 0 && subjects.length === 0)) return;
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      class_codes: classes,
      subject_codes: subjects,
      dimension_combination: combination,
    };
    if (search) {
      patch.mutate({ id: search.id, body }, { onSuccess: onClose });
    } else {
      create.mutate(
        { ...body, ...(theme ? { copy_from_theme: theme.id } : {}) },
        { onSuccess: (saved) => { onSaved?.(saved); onClose(); } },
      );
    }
  }

  return (
    <Card className="border-primary/40" data-testid="custom-search-editor">
      <CardHeader>
        <CardTitle>{search ? "Editar busca personalizada" : "Criar busca personalizada"}</CardTitle>
        <p className="text-sm text-muted-foreground">
          Cada salvamento de edição cria uma nova versão. Coletas anteriores mantêm seus critérios.
        </p>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="custom-search-name">Nome</Label>
              <Input
                id="custom-search-name"
                value={name}
                maxLength={160}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="custom-search-description">Descrição (opcional)</Label>
              <Input
                id="custom-search-description"
                value={description}
                maxLength={2000}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <CodeSelector
              dimension="class"
              label="Classes TPU"
              query={classQuery}
              onQuery={setClassQuery}
              items={codesForClasses.data ?? []}
              selected={classes}
              onChange={setClasses}
              loading={codesForClasses.isPending}
              error={codesForClasses.isError}
            />
            <CodeSelector
              dimension="subject"
              label="Assuntos TPU"
              query={subjectQuery}
              onQuery={setSubjectQuery}
              items={codesForSubjects.data ?? []}
              selected={subjects}
              onChange={setSubjects}
              loading={codesForSubjects.isPending}
              error={codesForSubjects.isError}
            />
          </div>

          <div className="space-y-1.5 sm:max-w-sm">
            <Label htmlFor="custom-search-combination">Combinar classes e assuntos</Label>
            <select
              id="custom-search-combination"
              className="h-10 w-full rounded-md border bg-background px-3 text-sm"
              value={combination}
              onChange={(event) => setCombination(event.target.value as "AND" | "OR")}
            >
              <option value="AND">Exigir todas as dimensões (AND)</option>
              <option value="OR">Aceitar qualquer dimensão (OR)</option>
            </select>
            <p className="text-xs text-muted-foreground">
              Códigos dentro de cada dimensão são combinados por correspondência a qualquer item.
            </p>
          </div>

          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertTitle>A busca não foi salva</AlertTitle>
              <AlertDescription>{describeError(error)}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={pending || !name.trim() || (!classes.length && !subjects.length)}>
              {pending ? "Salvando…" : search ? "Salvar nova versão" : "Salvar busca"}
            </Button>
            <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
              Cancelar
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function CodeSelector({
  dimension,
  label,
  query,
  onQuery,
  items,
  selected,
  onChange,
  loading,
  error,
}: {
  dimension: "class" | "subject";
  label: string;
  query: string;
  onQuery: (query: string) => void;
  items: Array<{ code: number; name: string }>;
  selected: number[];
  onChange: (codes: number[]) => void;
  loading: boolean;
  error: boolean;
}) {
  const title = dimension === "class" ? "classe" : "assunto";
  const missingSelections = selected.filter((code) => !items.some((item) => item.code === code));
  return (
    <fieldset className="min-w-0 space-y-2">
      <legend className="text-sm font-semibold">{label}</legend>
      <Label className="sr-only" htmlFor={`custom-${dimension}-search`}>Pesquisar {title} no catálogo</Label>
      <Input
        id={`custom-${dimension}-search`}
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        placeholder={`Pesquisar ${title} por código ou nome`}
      />
      <div className="max-h-56 space-y-1 overflow-y-auto rounded-md border p-2" aria-label={label}>
        {loading ? <p className="p-2 text-sm text-muted-foreground">Carregando catálogo…</p> : null}
        {error ? <p className="p-2 text-sm text-destructive" role="alert">Não foi possível carregar o catálogo TPU.</p> : null}
        {missingSelections.map((code) => (
          <label key={`inactive-${code}`} className="flex cursor-pointer items-start gap-2 rounded border border-warning/50 px-2 py-1.5 text-sm">
            <input
              type="checkbox"
              checked
              onChange={() => onChange(selected.filter((selectedCode) => selectedCode !== code))}
              className="mt-0.5 size-4 accent-primary"
            />
            <span><strong>{code}</strong> — não está disponível para novas buscas; remova para atualizar</span>
          </label>
        ))}
        {!loading && !error && items.length === 0 ? (
          <p className="p-2 text-sm text-muted-foreground">Nenhum código ativo encontrado.</p>
        ) : null}
        {items.map((item) => (
          <label key={item.code} className="flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted">
            <input
              type="checkbox"
              checked={selected.includes(item.code)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...selected, item.code].sort((a, b) => a - b)
                    : selected.filter((code) => code !== item.code),
                )
              }
              className="mt-0.5 size-4 accent-primary"
            />
            <span><strong>{item.code}</strong> — {item.name}</span>
          </label>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">Selecionados: {selected.length}</p>
    </fieldset>
  );
}
