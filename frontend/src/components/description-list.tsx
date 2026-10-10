import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type DescriptionItem = { term: string; value: ReactNode };

export function DescriptionList({
  items,
  className,
}: {
  items: DescriptionItem[];
  className?: string;
}) {
  return (
    <dl className={cn("grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3", className)}>
      {items.map((item) => (
        <div key={item.term} className="min-w-0">
          <dt className="text-xs font-medium text-muted-foreground">
            {item.term}
          </dt>
          <dd className="mt-0.5 text-sm wrap-break-word">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
