import { createContext, useContext } from "react";

import type { Environment } from "@/api/types";

export const EnvironmentContext = createContext<Environment | null>(null);

/** Data hooks are only rendered after the API identified its environment. */
export function useCurrentEnvironment(): Environment {
  const environment = useContext(EnvironmentContext);
  if (environment === null) {
    throw new Error("O ambiente da API ainda não foi identificado.");
  }
  return environment;
}
