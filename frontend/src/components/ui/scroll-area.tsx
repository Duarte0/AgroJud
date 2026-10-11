import * as React from "react";

import { cn } from "@/lib/utils";

type ScrollAreaProps = Omit<React.ComponentProps<"div">, "aria-label" | "role" | "tabIndex"> & {
  label: string;
};

export function ScrollArea({ label, className, ...props }: ScrollAreaProps) {
  return (
    <div
      role="region"
      aria-label={label}
      tabIndex={0}
      className={cn(
        "max-h-[min(70vh,48rem)] overflow-y-auto overscroll-contain focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        className,
      )}
      {...props}
    />
  );
}
