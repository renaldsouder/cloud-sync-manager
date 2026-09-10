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

export const fetchProviders = (
  includeAll: boolean,
  includeAdvanced = false,
  signal?: AbortSignal,
) =>
  request<Provider[]>(
    `/api/providers?include_all=${includeAll}&include_advanced=${includeAdvanced}`,
    { signal },
  );

export const fetchRemotes = (signal?: AbortSignal) =>
  request<Remote[]>("/api/remotes", { signal });

export const createRemote = (body: {
  name: string;
  provider: string;
  options: Record<string, string>;
}) => request<Remote>("/api/remotes", { method: "POST", body: JSON.stringify(body) });

export const completeRemote = (id: string) =>
  request<{ options: Record<string, string> }>(`/api/remotes/${id}/complete`, {
    method: "POST",
  });

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
  /** Étape en cours : contrôle de la source, simulation, transfert, purge. */
  phase: string;
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
  blocked_reason: string | null;
  deletion_plan: {
    deletes: number;
    checks: number;
    percent: number;
    paths: string[];
  } | null;
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
  next_run_at: string | null;
  schedule: Schedule | null;
  schedule_label: string;
  live: LiveRun | null;
};

export type Schedule = {
  kind: "manual" | "interval" | "daily" | "weekly";
  minutes?: number;
  time?: string;
  days?: number[];
  catch_up: "skip" | "once";
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
  summary: {
    blocked_reason?: string;
    deletion_plan?: { deletes: number; checks: number; percent: number };
  } | null;
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

export const runTask = (id: string, dryRun: boolean, confirmDeletions = false) =>
  request<Run>(`/api/tasks/${id}/run`, {
    method: "POST",
    body: JSON.stringify({ dry_run: dryRun, confirm_deletions: confirmDeletions }),
  });

export type TaskEvent = {
  id: number;
  kind: string;
  at: string;
  path: string | null;
  size: number | null;
  message: string | null;
};

export const fetchRun = (runId: string, signal?: AbortSignal) =>
  request<Run>(`/api/runs/${runId}`, { signal });

export const fetchEvents = (runId: string, kind?: string, signal?: AbortSignal) =>
  request<TaskEvent[]>(
    `/api/runs/${runId}/events${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`,
    { signal },
  );

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

export type Dashboard = {
  counters: {
    total: number;
    scheduled: number;
    running: number;
    warning: number;
    error: number;
    blocked: number;
    paused: number;
  };
  next_run_at: string | null;
  throughput_bytes_per_second: number;
  running: LiveRun[];
  recent_runs: Run[];
};

export const fetchDashboard = (signal?: AbortSignal) =>
  request<Dashboard>("/api/dashboard", { signal });

export const updateTask = (
  id: string,
  patch: { schedule?: Schedule | null; enabled?: boolean; name?: string },
) => request<Task>(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

export function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("fr-FR", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

export type FilterRule = { type: string; value: string };

export type FilterSet = {
  id: string;
  name: string;
  rules: FilterRule[];
  compiled: string[];
  task_count: number;
};

export type FilterPreview = {
  included: string[];
  excluded: { path: string; reason: string }[];
  truncated: boolean;
};

export type NotificationSettings = {
  unraid_url: string;
  unraid_api_key_configured: boolean;
  webhook_url: string;
  events: string[];
  allow_self_signed: boolean;
};

export const fetchFilterSets = (signal?: AbortSignal) =>
  request<FilterSet[]>("/api/filter-sets", { signal });

export const createFilterSet = (body: { name: string; rules: FilterRule[] }) =>
  request<FilterSet>("/api/filter-sets", { method: "POST", body: JSON.stringify(body) });

export const updateFilterSet = (id: string, body: { name: string; rules: FilterRule[] }) =>
  request<FilterSet>(`/api/filter-sets/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteFilterSet = (id: string) =>
  request<void>(`/api/filter-sets/${id}`, { method: "DELETE" });

export const previewFilterSet = (id: string, path: string) =>
  request<FilterPreview>(`/api/filter-sets/${id}/preview`, {
    method: "POST",
    body: JSON.stringify({ path }),
  });

export const fetchSettings = (signal?: AbortSignal) =>
  request<{ notifications: NotificationSettings; events: string[] }>("/api/settings", {
    signal,
  });

export const saveSettings = (body: {
  unraid_url?: string;
  unraid_api_key?: string;
  webhook_url?: string;
  events?: string[];
  allow_self_signed?: boolean;
}) =>
  request<{ notifications: NotificationSettings }>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const testNotifications = () =>
  request<{
    delivered: boolean;
    detail: string;
    results: { channel: string; ok: boolean; detail: string }[];
  }>("/api/settings/notifications/test", { method: "POST" });

export const exportConfig = () => request<Record<string, unknown>>("/api/config/export");

export const importConfig = (payload: unknown) =>
  request<{
    remotes_created: number;
    filter_sets_created: number;
    tasks_created: number;
    skipped: string[];
    warnings: string[];
  }>("/api/config/import", { method: "POST", body: JSON.stringify(payload) });

export type AuthState = { enabled: boolean; authenticated: boolean };

export const fetchAuthSession = (signal?: AbortSignal) =>
  request<AuthState>("/api/auth/session", { signal });

export const login = (password: string, remember: boolean) =>
  request<{ authenticated: boolean }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ password, remember }),
  });

export const logout = () =>
  request<{ authenticated: boolean }>("/api/auth/logout", { method: "POST" });

export const setPassword = (body: {
  current_password?: string;
  new_password: string;
}) => request<{ enabled: boolean }>("/api/auth/password", {
  method: "PUT",
  body: JSON.stringify(body),
});

export const startOAuth = (provider: string) =>
  request<{ session_id: string; auth_url: string; instructions: string }>(
    "/api/oauth/start",
    { method: "POST", body: JSON.stringify({ provider }) },
  );

export const completeOAuth = (sessionId: string, redirectUrl: string) =>
  request<{ options: Record<string, string>; error?: string }>(
    "/api/oauth/complete",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, redirect_url: redirectUrl }),
    },
  );

export const cancelOAuth = () =>
  request<void>("/api/oauth/cancel", { method: "POST" });
