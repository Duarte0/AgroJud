import { screen } from "@testing-library/react";

import { JobGroupPage } from "@/pages/job-group-page";
import { json, mockApi, renderRoute } from "@/test/render";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("JobGroupPage", () => {
  it("shows each tribunal separately and keeps successful child jobs accessible after partial completion", async () => {
    const groupId = "66666666-6666-4666-8666-666666666666";
    const completedJob = "11111111-1111-4111-8111-111111111111";
    mockApi({
      [`GET /api/v1/job-groups/${groupId}`]: () => json({
        id: groupId,
        kind: "discovery",
        state: "partial",
        matrix_sha256: "a".repeat(64),
        allowlist_version: "2026-10-11",
        catalog_version: "1.1.0",
        tpu_version: "06/10/2026",
        request_snapshot: { tribunals: ["TJMT", "TJMS"] },
        member_count: 2,
        completed_count: 1,
        created_at: "2026-10-12T12:00:00Z",
        updated_at: "2026-10-12T12:05:00Z",
        finished_at: "2026-10-12T12:05:00Z",
        members: [
          {
            id: "77777777-7777-4777-8777-777777777777",
            tribunal: "TJMT",
            state: "COMPLETED",
            availability_state: "VALIDATED",
            reason: null,
            job_id: completedJob,
            collection_id: "22222222-2222-4222-8222-222222222222",
            coverage: {
              hits_confirmed: 4,
              pages_confirmed: 1,
              valid_hits: 4,
              rejected_hits: 0,
              new: 4,
              updated: 0,
              unchanged: 0,
              coverage: "complete",
            },
            next_page: 2,
            checkpoint_revision: 1,
            checkpoint_updated_at: "2026-10-12T12:05:00Z",
            created_at: "2026-10-12T12:00:00Z",
            updated_at: "2026-10-12T12:05:00Z",
          },
          {
            id: "88888888-8888-4888-8888-888888888888",
            tribunal: "TJMS",
            state: "FAILED",
            availability_state: "VALIDATED",
            reason: "source_timeout",
            job_id: "99999999-9999-4999-8999-999999999999",
            collection_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            coverage: { hits_confirmed: 2, pages_confirmed: 1, coverage: "partial" },
            next_page: 2,
            checkpoint_revision: 1,
            checkpoint_updated_at: "2026-10-12T12:03:00Z",
            created_at: "2026-10-12T12:00:00Z",
            updated_at: "2026-10-12T12:03:00Z",
          },
        ],
      }),
    });
    renderRoute(<JobGroupPage />, {
      path: "/job-groups/:groupId",
      url: `/job-groups/${groupId}`,
    });

    expect(await screen.findByText("Resultado concluído parcialmente")).toBeInTheDocument();
    const members = screen.getAllByTestId("job-group-member");
    expect(members).toHaveLength(2);
    expect(members[0]).toHaveAttribute("data-tribunal", "TJMT");
    expect(screen.getByText("source_timeout")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Abrir execução" })[0]).toHaveAttribute(
      "href",
      `/jobs/${completedJob}`,
    );
    expect(screen.getByRole("progressbar", { name: "Tribunais concluídos" })).toHaveAttribute(
      "aria-valuenow",
      "1",
    );
  });
});
