export type Check = {
  ok: boolean;
  detail?: string;
  version?: string;
  os?: string;
  arch?: string;
};

export type Health = {
  status: "ok" | "degraded";
  version: string;
  checks: Record<string, Check>;
};

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch("/api/health", { signal });
  if (!response.ok) {
    throw new Error(`L'API a répondu ${response.status}`);
  }
  return (await response.json()) as Health;
}
