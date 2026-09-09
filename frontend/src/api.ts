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

export type LiveRun = {
  run_id: string;
  task_id: string;
  task_name: string;
  dry_run: boolean;
  status: string;
  current_file: string | null;
  counters: { transfers: number; deletes: number; errors: number };
  stats: {
    bytes?: number;
    total_bytes?: number;
    transfers?: number;
    total_transfers?: number;
    speed?: number;
    eta?: number | null;
    errors?: number;
  };
  last_error: string | null;
};

export type Task = {
  id: string;
  name: string;
  remote_id: string;
  local_path: string;
  remote_path: string;
  direction: "local_to_remote" | "remote_to_local";
  mode: "copy" | "mirror" | "bisync";
  delete_policy: string;
  quarantine_enabled: boolean;
  dry_run_required: boolean;
  enabled: boolean;
  status: string;
  last_run_id: string | null;
  live: LiveRun | null;
};

export type Run = {
  id: string;
  task_id: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  exit_code: number | null;
  transferred_files: number;
  transferred_bytes: number;
  deleted_files: number;
  errors_count: number;
  dry_run: boolean;
  rclone_version: string | null;
  live: LiveRun | null;
};

export const fetchTasks = (signal?: AbortSignal) =>
  request<Task[]>("/api/tasks", { signal });

export const createTask = (body: {
  name: string;
  remote_id: string;
  local_path: string;
  remote_path: string;
  direction: string;
  mode: string;
}) => request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(body) });

export const deleteTask = (id: string) =>
  request<void>(`/api/tasks/${id}`, { method: "DELETE" });

export const runTask = (id: string, dryRun: boolean) =>
  request<Run>(`/api/tasks/${id}/run`, {
    method: "POST",
    body: JSON.stringify({ dry_run: dryRun }),
  });

export const stopRun = (runId: string) =>
  request<Run>(`/api/runs/${runId}/stop`, { method: "POST" });

export const fetchRuns = (taskId: string, signal?: AbortSignal) =>
  request<Run[]>(`/api/tasks/${taskId}/runs`, { signal });

/** Flux SSE de progression (UI-003). Rend une fonction de désabonnement. */
export function subscribeToRuns(onUpdate: (runs: LiveRun[]) => void): () => void {
  const source = new EventSource("/api/stream/runs");
  source.onmessage = (event) => {
    try {
      onUpdate(JSON.parse(event.data) as LiveRun[]);
    } catch {
      /* trame incomplète : la suivante arrive dans une seconde */
    }
  };
  return () => source.close();
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  const units = ["Kio", "Mio", "Gio", "Tio"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}
