import { availableJobActions } from "@/lib/job-actions";
import { buildJob } from "@/test/factories";

const commands = (overrides: Parameters<typeof buildJob>[0]) =>
  availableJobActions(buildJob(overrides)).map((action) => action.command);

describe("availableJobActions", () => {
  it("offers cancel only while active and not already requested", () => {
    expect(commands({ status: "running" })).toEqual(["cancel"]);
    expect(commands({ status: "running", cancel_requested: true })).toEqual([]);
  });

  it("offers resume for failed or cancelled jobs", () => {
    expect(commands({ status: "failed" })).toEqual(["resume"]);
    expect(commands({ status: "cancelled" })).toEqual(["resume"]);
  });

  it("offers continue only for partial jobs that reached the limit", () => {
    expect(commands({ status: "partial", reason: "limit" })).toEqual(["continue"]);
    expect(commands({ status: "partial", reason: "rejections" })).toEqual([]);
  });

  it("requires an explicit restart when the cursor was invalidated", () => {
    expect(commands({ status: "failed", cursor_invalid: true })).toEqual(["restart-scan"]);
  });

  it("offers nothing for completed jobs", () => {
    expect(commands({ status: "completed" })).toEqual([]);
  });
});
