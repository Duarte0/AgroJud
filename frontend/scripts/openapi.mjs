import { spawnSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(frontendRoot, "..");
const backendRoot = resolve(repositoryRoot, "backend");
const schemaPath = resolve(frontendRoot, "openapi.json");
const typePath = resolve(frontendRoot, "src/api-schema.ts");
const checkOnly = process.argv.includes("--check");
const pythonEnvironment = {
  ...process.env,
  AGROJUD_ENV: "demo",
  DATABASE_URL:
    "postgresql+psycopg://agrojud:contract-only@127.0.0.1:5432/agrojud_demo",
  DATAJUD_API_KEY: "",
};

const exported = spawnSync(
  "uv",
  [
    "run",
    "--project",
    backendRoot,
    "--frozen",
    "--no-dev",
    "python",
    "-m",
    "agrojud.api.export_openapi",
    "--stdout",
  ],
  { cwd: backendRoot, encoding: "utf8", env: pythonEnvironment },
);
if (exported.error) {
  throw new Error(`Não foi possível executar uv para gerar OpenAPI: ${exported.error.message}`);
}
if (exported.status !== 0) {
  process.stderr.write(exported.stderr);
  process.exit(exported.status ?? 1);
}

const generatedSchema = exported.stdout;
const temporaryDirectory = mkdtempSync(resolve(tmpdir(), "agrojud-openapi-"));
const temporarySchema = resolve(temporaryDirectory, "openapi.json");
const temporaryTypes = resolve(temporaryDirectory, "api-schema.ts");
mkdirSync(dirname(typePath), { recursive: true });
writeFileSync(temporarySchema, generatedSchema, "utf8");

try {
  const outputTypes = checkOnly ? temporaryTypes : typePath;
  const typed = spawnSync(
    "npm",
    ["exec", "--", "openapi-typescript", temporarySchema, "-o", outputTypes],
    { cwd: frontendRoot, encoding: "utf8" },
  );
  if (typed.error) {
    throw new Error(`Não foi possível executar openapi-typescript: ${typed.error.message}`);
  }
  if (typed.status !== 0) {
    process.stderr.write(typed.stderr);
    process.exit(typed.status ?? 1);
  }

  if (checkOnly) {
    const checkedSchema = readFileSync(schemaPath, "utf8");
    const checkedTypes = readFileSync(typePath, "utf8");
    if (checkedSchema !== generatedSchema) {
      process.stderr.write(
        "frontend/openapi.json diverge do schema exportado; execute npm run openapi:generate.\n",
      );
      process.exitCode = 1;
    }
    if (checkedTypes !== readFileSync(temporaryTypes, "utf8")) {
      process.stderr.write(
        "frontend/src/api-schema.ts diverge dos tipos gerados; execute npm run openapi:generate.\n",
      );
      process.exitCode = 1;
    }
    if (process.exitCode !== 1) {
      process.stdout.write("OpenAPI e tipos TypeScript estão atualizados.\n");
    }
  } else {
    writeFileSync(schemaPath, generatedSchema, "utf8");
    process.stdout.write("Schema OpenAPI e tipos TypeScript gerados.\n");
  }
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
