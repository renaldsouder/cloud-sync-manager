import type { Onboarding } from "./api";

type Etape = {
  cle: keyof Onboarding;
  titre: string;
  detail: string;
  onglet: "parametres" | "stockages" | "taches";
  action: string;
};

const ETAPES: Etape[] = [
  {
    cle: "password",
    titre: "Protéger l'interface",
    detail:
      "Sans mot de passe, toute personne joignant ce port sur votre réseau peut créer une tâche et déclencher des suppressions.",
    onglet: "parametres",
    action: "Définir un mot de passe",
  },
  {
    cle: "remote_tested",
    titre: "Connecter un stockage Cloud",
    detail:
      "Ajoutez votre fournisseur, puis testez-le. Une connexion qui répond ne garantit pas que toutes les opérations soient supportées : le test le dit.",
    onglet: "stockages",
    action: "Ajouter un stockage",
  },
  {
    cle: "task",
    titre: "Créer une première tâche",
    detail:
      "Un dossier local, un dossier distant, un sens. Commencez par une Copie : elle n'efface jamais rien à destination.",
    onglet: "taches",
    action: "Créer une tâche",
  },
  {
    cle: "simulation",
    titre: "Lancer une simulation",
    detail:
      "Elle liste ce qui serait transféré et supprimé, sans rien écrire. C'est le moment de vérifier que les dossiers sont les bons.",
    onglet: "taches",
    action: "Simuler",
  },
];

type Props = {
  etat: Onboarding;
  onAller: (onglet: Etape["onglet"]) => void;
  onIgnorer: () => void;
};

/**
 * Assistant de première utilisation (FIRST-001, §3).
 *
 * Chaque étape existait déjà ; il manquait ce qui les enchaîne. L'ordre
 * n'est pas décoratif : protéger l'interface vient avant de créer quoi que
 * ce soit, et la simulation avant toute exécution réelle.
 *
 * L'état est déduit du contenu de l'application, pas d'un compteur : une
 * étape se décoche si l'on supprime ce qu'elle avait produit.
 */
export default function WelcomeView({ etat, onAller, onIgnorer }: Props) {
  const faites = ETAPES.filter((etape) => etat[etape.cle]).length;
  const suivante = ETAPES.find((etape) => !etat[etape.cle]);

  return (
    <section className="card">
      <div className="card__head">
        <h2>Bienvenue</h2>
        <span className="chip">
          {faites} sur {ETAPES.length}
        </span>
      </div>

      <p>
        Cloud Sync Manager synchronise vos partages Unraid avec un stockage
        Cloud. Quatre étapes suffisent, et la dernière vous montre ce qui se
        passerait avant que quoi que ce soit ne bouge.
      </p>

      <ol className="etapes">
        {ETAPES.map((etape) => {
          const faite = Boolean(etat[etape.cle]);
          const courante = suivante?.cle === etape.cle;
          return (
            <li
              key={etape.cle}
              className={faite ? "etape etape--faite" : "etape"}
            >
              <span className="etape__marque" aria-hidden="true">
                {faite ? "✓" : "•"}
              </span>
              <span className="etape__corps">
                <strong>{etape.titre}</strong>
                <span className="field__help">{etape.detail}</span>
                {!faite && (
                  <span className="actions">
                    <button
                      type="button"
                      className={courante ? "btn btn--small" : "btn btn--small btn--ghost"}
                      onClick={() => onAller(etape.onglet)}
                    >
                      {etape.action}
                    </button>
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="actions">
        <button type="button" className="btn btn--ghost btn--small" onClick={onIgnorer}>
          Ne plus afficher
        </button>
      </div>
    </section>
  );
}
