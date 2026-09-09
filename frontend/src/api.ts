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

export type ProviderOption = {
  name: string;
  help: string;
  help_full: string;
  type: string;
  required: boolean;
  advanced: boolean;
  is_password: boolean;
  sensitive: boolean;
  default: string | null;
  examples: { value?: string; help?: string }[];
};

export type Provider = {
  name: string;
  label: string;
  description: string;
  priority: boolean;
  needs_oauth: boolean;
  options: ProviderOption[];
};

export type Remote = {
  id: string;
  name: string;
  provider: string;
  rclone_remote_name: string;
  status: "unknown" | "ok" | "error";
  last_test_at: string | null;
  capabilities: Record<string, unknown> | null;
  task_count: number;
  options: Record<string, string>;
};

export type RemoteTest = {
  ok: boolean;
  detail: string;
  elapsed_ms: number;
  capabilities: Record<string, unknown>;
  entries: string[];
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });

  if (!response.ok) {
    // Le §27.10 interdit de réduire une erreur à « Échec » : on remonte le
    // message du serveur, qui porte la cause technique.
    let detail = `L'API a répondu ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      /* réponse sans corps JSON */
    }
    throw new Error(detail);
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const fetchHealth = (signal?: AbortSignal) =>
  request<Health>("/api/health", { signal });

export const fetchProviders = (includeAll: boolean, signal?: AbortSignal) =>
  request<Provider[]>(`/api/providers?include_all=${includeAll}`, { signal });

export const fetchRemotes = (signal?: AbortSignal) =>
  request<Remote[]>("/api/remotes", { signal });

export const createRemote = (body: {
  name: string;
  provider: string;
  options: Record<string, string>;
}) => request<Remote>("/api/remotes", { method: "POST", body: JSON.stringify(body) });

export const testRemote = (id: string) =>
  request<RemoteTest>(`/api/remotes/${id}/test`, { method: "POST" });

export const deleteRemote = (id: string) =>
  request<void>(`/api/remotes/${id}`, { method: "DELETE" });
