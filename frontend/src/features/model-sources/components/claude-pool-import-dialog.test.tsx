import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import { ClaudePoolImportDialog } from "./claude-pool-import-dialog";

describe("ClaudePoolImportDialog", () => {
  it("previews account names and imports an Opus pool without displaying credentials", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithProviders(
      <ClaudePoolImportDialog
        open
        busy={false}
        source={null}
        onOpenChange={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn()}
      />,
    );
    const file = new File(
      [
        JSON.stringify({
          accounts: [
            {
              id: "one",
              name: "My Claude account",
              accessToken: "private-access",
              refreshToken: "private-refresh",
              expires_at_ms: 1_900_000_000_000,
            },
          ],
        }),
      ],
      "config.json",
      { type: "application/json" },
    );
    await user.upload(screen.getByLabelText("Account file"), file);
    await screen.findByText("My Claude account");
    expect(screen.queryByText("private-access")).not.toBeInTheDocument();
    expect(screen.queryByText("private-refresh")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Import accounts" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());
    expect(onCreate.mock.calls[0][0]).toMatchObject({
      kind: "anthropic",
      models: [{ model: "claude-opus-5-5" }],
      anthropicAccounts: [
        { id: "one", name: "My Claude account", accessToken: "private-access" },
      ],
    });
  });

  it("rejects invalid files without displaying secret-bearing validation errors", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ClaudePoolImportDialog
        open
        busy={false}
        source={null}
        onOpenChange={vi.fn()}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
      />,
    );
    await user.upload(
      screen.getByLabelText("Account file"),
      new File(
        ['{"accounts":[{"accessToken":"private-access"}]}'],
        "config.json",
        { type: "application/json" },
      ),
    );
    await screen.findByText(
      "Choose a valid claude-lb config.json account file.",
    );
    expect(
      screen.getByRole("button", { name: "Import accounts" }),
    ).toBeDisabled();
    expect(screen.queryByText("private-access")).not.toBeInTheDocument();
  });
});
