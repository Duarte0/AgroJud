import { defineConfig, devices } from "@playwright/test";

const apiUrl = process.env.AGROJUD_API_URL;
const externalBaseURL = process.env.PLAYWRIGHT_BASE_URL;
if (!externalBaseURL && !apiUrl) {
  throw new Error("Defina AGROJUD_API_URL ou PLAYWRIGHT_BASE_URL; use scripts/e2e.sh para o fluxo Compose.");
}
const viteWebServer = apiUrl
  ? {
      command: "npm run build && npx vite preview",
      url: "http://127.0.0.1:4173/radar",
      reuseExistingServer: false,
      timeout: 120_000,
      env: { AGROJUD_API_URL: apiUrl },
    }
  : undefined;

export default defineConfig({
  testDir: "e2e",
  // The suites share one worker process and one database; run them serially.
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: externalBaseURL ?? "http://127.0.0.1:4173",
    locale: "pt-BR",
    timezoneId: "America/Sao_Paulo",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: externalBaseURL ? undefined : viteWebServer,
});
