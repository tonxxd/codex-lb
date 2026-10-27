import { useRef, useState } from "react";
import { z } from "zod";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ClaudeAccountInputSchema,
  type ModelSource,
  type ModelSourceCreateRequest,
  type ModelSourceUpdateRequest,
} from "@/features/model-sources/schemas";

const LegacyPoolSchema = z.object({
  accounts: z
    .array(
      z.object({
        id: z.string().min(1),
        name: z.string().min(1),
        accessToken: z.string().min(1),
        refreshToken: z.string().min(1),
        expires_at_ms: z.number().positive(),
      }),
    )
    .min(1)
    .max(50),
});

type Props = {
  open: boolean;
  busy: boolean;
  source: ModelSource | null;
  onOpenChange: (open: boolean) => void;
  onCreate: (payload: ModelSourceCreateRequest) => Promise<void>;
  onUpdate: (id: string, payload: ModelSourceUpdateRequest) => Promise<void>;
};

export function ClaudePoolImportDialog({
  open,
  busy,
  source,
  onOpenChange,
  onCreate,
  onUpdate,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [accounts, setAccounts] = useState<
    z.infer<typeof ClaudeAccountInputSchema>[]
  >([]);
  const [error, setError] = useState<string | null>(null);

  const close = (value: boolean) => {
    if (!value) {
      setAccounts([]);
      setError(null);
      if (fileInput.current) fileInput.current.value = "";
    }
    onOpenChange(value);
  };

  const selectFile = async (file?: File) => {
    setAccounts([]);
    setError(null);
    if (!file) return;
    if (file.size > 1_048_576) {
      setError("Choose an account file smaller than 1 MB.");
      return;
    }
    try {
      const pool = LegacyPoolSchema.parse(JSON.parse(await file.text()));
      setAccounts(
        pool.accounts.map((account) =>
          ClaudeAccountInputSchema.parse({
            id: account.id,
            name: account.name,
            accessToken: account.accessToken,
            refreshToken: account.refreshToken,
            expiresAt: new Date(account.expires_at_ms).toISOString(),
          }),
        ),
      );
    } catch {
      // Validation errors may contain token values: display a fixed message.
      setError("Choose a valid claude-lb config.json account file.");
    }
  };

  const submit = async () => {
    try {
      if (source) {
        await onUpdate(source.id, { anthropicAccounts: accounts });
      } else {
        await onCreate({
          kind: "anthropic",
          name: "Claude account pool",
          baseUrl: "https://api.anthropic.com/v1",
          supportsChatCompletions: false,
          supportsResponses: true,
          anthropicAccounts: accounts,
          models: [
            {
              model: "claude-opus-5-5",
              displayName: "Claude Opus 5.5",
              contextWindow: 1_000_000,
              maxOutputTokens: 128_000,
              supportsStreaming: true,
              supportsTools: true,
              supportsVision: true,
              rawMetadataJson: JSON.stringify({
                supports_reasoning: true,
                supports_reasoning_summaries: true,
                supported_reasoning_levels: ["low", "medium", "high", "max"],
                default_reasoning_level: "medium",
              }),
            },
          ],
        });
      }
      close(false);
    } catch {
      setError(
        "The account pool could not be saved. Check the source settings and try again.",
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {source ? "Replace Claude accounts" : "Import Claude accounts"}
          </DialogTitle>
          <DialogDescription>
            Import your claude-lb account file. Accounts refresh automatically
            and fail over in the imported order.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="claude-pool-file">Account file</Label>
          <Input
            ref={fileInput}
            id="claude-pool-file"
            type="file"
            accept=".json,application/json"
            disabled={busy}
            onChange={(event) => void selectFile(event.target.files?.[0])}
          />
          {accounts.length > 0 ? (
            <ul className="space-y-1 text-sm">
              {accounts.map((account) => (
                <li key={account.id}>{account.name}</li>
              ))}
            </ul>
          ) : null}
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
        </div>
        <DialogFooter>
          <Button
            disabled={busy || accounts.length === 0}
            onClick={() => void submit()}
          >
            {source ? "Replace accounts" : "Import accounts"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
