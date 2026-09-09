import { useCallback, useEffect, useRef, useState } from "react";

import FiltersPanel from "./FiltersPanel";
import {
  exportConfig,
  fetchSettings,
  importConfig,
  saveSettings,
  testNotifications,
  type NotificationSettings,
} from "./api";

const EVENT_LABELS: Record<string, string> = {
  failure: "Échec d'une synchronisation",
  blocked: "Tâche bloquée, en attente de validation",
  auth_expired: "Authentification expirée",
  success: "Succès (bruyant, désactivé par défaut)",
};

export default function SettingsView() {
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    try {
      const payload = await fetchSettings();
      setSettings(payload.notifications);
      setEvents(payload.events);
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function run(action: () => Promise<unknown>, success?: string) {
    setError(null);
    setMessage(null);
    try {
      await action();
      if (success) setMessage(success);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function save() {
    if (!settings) return;
    await run(async () => {
      await saveSettings({
        unraid_url: settings.unraid_url,
        webhook_url: settings.webhook_url,
        events: settings.events,
        // Champ laissé vide : la clé déjà enregistrée est conservée.
        ...(apiKey ? { unraid_api_key: apiKey } : {}),
      });
      setApiKey("");
      await reload();
    }, "Paramètres enregistrés.");
  }

  async function download() {
    await run(async () => {
      const payload = await exportConfig();
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `cloud-sync-manager-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    });
  }

  async function upload(file: File) {
    await run(async () => {
      const report = await importConfig(JSON.parse(await file.text()));
      const lines = [
        `${report.remotes_created} stockage(s), ${report.filter_sets_created} jeu(x) de filtres, ${report.tasks_created} tâche(s) importée(s).`,
        ...report.warnings,
        ...report.skipped,
      ];
      setMessage(lines.join(" "));
    });
  }

  return (
    <>
      <section className="card">
        <h2>Notifications</h2>
        {error && <p className="state state--error">{error}</p>}
        {message && <p className="state">{message}</p>}
        {!settings && !error && <p className="state">Chargement…</p>}

        {settings && (
          <>
            <label className="field">
              <span className="field__label">Adresse du serveur Unraid</span>
              <input
                value={settings.unraid_url}
                placeholder="http://192.168.1.10"
                onChange={(event) =>
                  setSettings({ ...settings, unraid_url: event.target.value })
                }
              />
              <span className="field__help">
                Laisser vide pour désactiver les notifications Unraid. Nécessite
                l'API intégrée à Unraid 7.2 ou plus récent.
              </span>
            </label>

            <label className="field">
              <span className="field__label">Clé d'API Unraid</span>
              <input
                type="password"
                value={apiKey}
                autoComplete="new-password"
                placeholder={
                  settings.unraid_api_key_configured
                    ? "configurée — laisser vide pour conserver"
                    : "non configurée"
                }
                onChange={(event) => setApiKey(event.target.value)}
              />
              <span className="field__help">
                Jamais réaffichée ni incluse dans une sauvegarde.
              </span>
            </label>

            <label className="field">
              <span className="field__label">Webhook</span>
              <input
                value={settings.webhook_url}
                placeholder="https://exemple/hook"
                onChange={(event) =>
                  setSettings({ ...settings, webhook_url: event.target.value })
                }
              />
              <span className="field__help">
                Reçoit un POST JSON. Home Assistant, Discord, ou autre.
              </span>
            </label>

            <fieldset className="choices">
              <legend className="field__label">Événements notifiés</legend>
              {events.map((event) => (
                <label className="choice" key={event}>
                  <input
                    type="checkbox"
                    checked={settings.events.includes(event)}
                    onChange={(changed) =>
                      setSettings({
                        ...settings,
                        events: changed.target.checked
                          ? [...settings.events, event]
                          : settings.events.filter((value) => value !== event),
                      })
                    }
                  />
                  <span>{EVENT_LABELS[event] ?? event}</span>
                </label>
              ))}
            </fieldset>

            <div className="actions">
              <button type="button" className="btn" onClick={() => void save()}>
                Enregistrer
              </button>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() =>
                  void run(async () => {
                    const result = await testNotifications();
                    setMessage(result.detail);
                  })
                }
              >
                Envoyer un test
              </button>
            </div>
          </>
        )}
      </section>

      <FiltersPanel />

      <section className="card">
        <h2>Sauvegarde de la configuration</h2>
        <p className="state">
          L'export contient vos stockages, tâches, filtres et réglages —{" "}
          <strong>mais aucun identifiant Cloud</strong>. Une sauvegarde finit sur une
          clé USB ou dans un dépôt : y disséminer des jetons serait pire que
          l'inconvénient de les ressaisir. À la restauration, les tâches arrivent en
          pause, le temps que vous vérifiiez chemins et stockages.
        </p>
        <div className="actions">
          <button type="button" className="btn" onClick={() => void download()}>
            Exporter
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => fileInput.current?.click()}
          >
            Restaurer un fichier
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
              event.target.value = "";
            }}
          />
        </div>
      </section>
    </>
  );
}
