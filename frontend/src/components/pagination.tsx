import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatCount } from "@/lib/format";
import { totalPages } from "@/lib/search-params";

type PaginationProps = {
  label: string;
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  disabled?: boolean;
};

export function Pagination({
  label,
  page,
  pageSize,
  total,
  onPageChange,
  disabled = false,
}: PaginationProps) {
  const pages = totalPages(total, pageSize);
  return (
    <nav
      aria-label={label}
      className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground"
    >
      <span>
        Página {formatCount(page)} de {formatCount(pages)} · {formatCount(total)}{" "}
        {total === 1 ? "registro" : "registros"}
      </span>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={disabled || page <= 1}
        >
          <ChevronLeft aria-hidden="true" />
          Anterior
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={disabled || page >= pages}
        >
          Próxima
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}
