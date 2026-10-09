import { Link } from "react-router";

import { PageHeader } from "@/components/page-header";
import { usePageTitle } from "@/hooks/use-page-title";
import { Button } from "@/components/ui/button";

export function NotFoundPage({
  title = "Página não encontrada",
  description = "O endereço informado não corresponde a nenhuma tela.",
  backTo = "/radar",
  backLabel = "Ir para o radar",
}: {
  title?: string;
  description?: string;
  backTo?: string;
  backLabel?: string;
}) {
  usePageTitle(title);
  return (
    <div data-state="not-found">
      <PageHeader title={title} description={description} />
      <Button asChild variant="outline">
        <Link to={backTo}>{backLabel}</Link>
      </Button>
    </div>
  );
}
