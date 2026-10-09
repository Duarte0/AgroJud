import type { JobStatus } from "@/api/types";
import { Badge, type badgeVariants } from "@/components/ui/badge";
import { jobStatusLabel } from "@/lib/format";
import type { VariantProps } from "class-variance-authority";

type Variant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

const VARIANTS: Record<JobStatus, Variant> = {
  queued: "secondary",
  running: "info",
  retry_wait: "warning",
  completed: "success",
  partial: "warning",
  failed: "destructive",
  cancelled: "outline",
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <Badge variant={VARIANTS[status]} data-status={status}>
      {jobStatusLabel(status)}
    </Badge>
  );
}
