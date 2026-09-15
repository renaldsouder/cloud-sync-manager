import type { ReactNode } from "react";

/**
 * Aide intégrée (AIDE-001, AIDE-002).
 *
 * Les explications vivent ici en morceaux réutilisables plutôt que dans une
 * page unique : la moitié de leur utilité est d'être lisibles au moment du
 * choix, dans l'assistant lui-même, et non dans un onglet qu'il faudrait
 * penser à ouvrir avant.
 *
 * Le vocabulaire suit exactement celui de l'interface (§7). Appeler ici
 * « upload » ce que l'écran nomme « Local → Cloud » ferait chercher un
 * bouton qui n'existe pas.
 */

type Props = {
  /** Le bidirectionnel n'est décrit comme disponible que s'il l'est (§21). */
  bidirectional?: boolean;
};

/** Bloc d'aide repliable, pour glisser une explication sans encombrer. */
export function Repli({ titre, children }: { titre: string; children: ReactNode }) {
  return (
    <details className="aide__repli">
      <summary>{titre}</summary>
      <div className="aide__contenu">{children}</div>
    </details>
  );
}

export function AideModes({ bidirectional = false }: Props) {
  return (
    <div className="aide">
      <p>
        Deux choix décident de tout : le <strong>sens</strong>, qui désigne la
        source et la destination, et le <strong>mode</strong>, qui décide si la
        destination a le droit de perdre des fichiers.
      </p>

      <h4>Le sens désigne le côté qui peut être modifié</h4>
      <p>
        <strong>Local → Cloud</strong> lit votre share Unraid et écrit chez le
        fournisseur : c'est le contenu du Cloud qui peut changer.{" "}
        <strong>Cloud → Local</strong> fait l'inverse, et c'est alors votre
        serveur qui est écrit. En Miroir, le sens décide donc de quel côté des
        fichiers peuvent disparaître.
      </p>

      <h4>Le mode décide des suppressions</h4>
      <div className="aide__table">
        <table>
          <thead>
            <tr>
              <th>Mode</th>
              <th>Ajoute et met à jour</th>
              <th>Supprime</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Copie</th>
              <td>à la destination</td>
              <td>jamais</td>
            </tr>
            <tr>
              <th scope="row">Miroir</th>
              <td>à la destination</td>
              <td>
                à la destination, tout ce qui n'existe plus à la source
              </td>
            </tr>
            <tr>
              <th scope="row">Bidirectionnel</th>
              <td>des deux côtés</td>
              <td>des deux côtés</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p>
        Une <strong>Copie</strong> ne retire jamais rien : un fichier effacé à
        la source reste à destination. C'est le mode à choisir quand on hésite.
      </p>
      <p>
        Un <strong>Miroir</strong> rend la destination identique à la source.
        C'est utile, et c'est le mode qui peut faire perdre des données si la
        source disparaît : les protections décrites plus bas existent pour ce
        cas précis.
      </p>
      <p>
        Le <strong>Bidirectionnel</strong> fait circuler les modifications dans
        les deux sens. Il demande une initialisation explicite, qui fusionne
        les deux côtés, et conserve les deux versions d'un fichier modifié de
        part et d'autre — sous deux noms suffixés, le nom d'origine
        disparaissant alors des deux côtés.
        {!bidirectional && (
          <>
            {" "}
            <strong>
              Ce mode n'est pas ouvert sur cette installation
            </strong>{" "}
            : il attend la fin de ses tests destructifs. À moitié fiable, il
            perdrait des données.
          </>
        )}
      </p>
    </div>
  );
}

export function AideSuppressions() {
  return (
    <div className="aide">
      <p>
        Rien de destructif ne se produit sans avoir été mesuré d'abord. Une
        exécution de Miroir se déroule en trois temps.
      </p>
      <ol>
        <li>
          <strong>Contrôle de la source.</strong> Si elle est inaccessible, ou
          vide alors qu'elle ne devrait pas l'être, la tâche échoue{" "}
          <em>sans propager la moindre suppression</em>. Un share démonté ne
          doit jamais se traduire par un Cloud vidé.
        </li>
        <li>
          <strong>Simulation de contrôle.</strong> Avant qu'un fichier ne
          bouge, l'application compte ce qui serait supprimé. Au-delà du seuil
          de la tâche — un nombre, un pourcentage de la destination, ou les
          deux — elle s'arrête sur{" "}
          <strong>« Bloquée — validation nécessaire »</strong> et nomme les
          fichiers concernés. Rien n'est touché tant que vous n'avez pas
          confirmé.
        </li>
        <li>
          <strong>Transfert.</strong> Les fichiers supprimés ne sont pas
          détruits : ils sont déplacés dans une corbeille.
        </li>
      </ol>

      <h4>Où vont les fichiers supprimés</h4>
      <p>
        Dans un dossier <code>.cloudsync-trash</code> créé à la destination,
        avec un sous-dossier daté par exécution. Ces corbeilles sont conservées
        30 jours, et les trois dernières le restent quoi qu'il arrive. Pour
        récupérer un fichier, on le reprend là où il est, avec l'explorateur
        habituel de la destination.
      </p>
      <p>
        La corbeille peut être désactivée sur une tâche. Les suppressions
        deviennent alors définitives, et une nouvelle simulation est exigée
        avant l'exécution suivante.
      </p>

      <h4>Quand une simulation est exigée</h4>
      <p>
        Avant la première exécution réelle d'un Miroir, et de nouveau après
        chaque changement qui modifie ce qui sera comparé : dossier source,
        dossier de destination, sens, ou retrait de la corbeille. La simulation
        emprunte exactement la même commande que l'exécution réelle, à
        l'exception de l'écriture — ce qu'elle annonce est donc ce qui se
        produira.
      </p>
    </div>
  );
}

export function AideStockage() {
  return (
    <div className="aide">
      <ol>
        <li>
          Onglet <strong>Stockages Cloud</strong>, puis{" "}
          <strong>Ajouter un stockage</strong>.
        </li>
        <li>
          Choisissez le fournisseur. Ceux marqués{" "}
          <em>« autorisation navigateur »</em> — OneDrive, Google Drive,
          Dropbox… — demandent une connexion chez eux plutôt qu'un mot de
          passe saisi ici.
        </li>
        <li>
          Cliquez sur <strong>Autoriser l'accès</strong>. Un onglet s'ouvre sur
          la page de connexion du fournisseur ; connectez-vous et acceptez.
        </li>
        <li>
          Votre navigateur atterrit ensuite sur une <strong>page d'erreur</strong> —
          c'est normal. Copiez l'adresse complète de cette page et collez-la
          dans le champ prévu, puis terminez l'autorisation.
        </li>
        <li>
          Donnez un nom au stockage, enregistrez, puis{" "}
          <strong>testez la connexion</strong> avant de créer une tâche. Un
          stockage qui ne répond pas se découvre mieux ici qu'au milieu d'un
          transfert nocturne.
        </li>
      </ol>
      <p className="state state--error">
        L'adresse de la page d'erreur contient un code d'autorisation : il
        n'a sa place que dans cette application. Ne le transmettez à personne,
        ne le collez dans aucun autre service.
      </p>
      <p>
        Les identifiants sont rangés dans le fichier de configuration de
        rclone, dans l'appdata du conteneur. Ils ne sont jamais réaffichés,
        jamais inclus dans une sauvegarde de configuration, jamais écrits dans
        les journaux.
      </p>
    </div>
  );
}

export function AideTache() {
  return (
    <div className="aide">
      <ol>
        <li>
          Onglet <strong>Tâches</strong>, puis <strong>Nouvelle tâche</strong>.
        </li>
        <li>
          Nommez-la, choisissez le stockage Cloud, le <strong>sens</strong> et
          le <strong>mode</strong>.
        </li>
        <li>
          Désignez les dossiers. <strong>Parcourir les dossiers…</strong> ouvre
          l'arborescence de vos shares Unraid,{" "}
          <strong>Parcourir le stockage distant…</strong> celle du Cloud. Un
          dossier distant qui n'existe pas encore peut être saisi à la main :
          la tâche le créera.
        </li>
        <li>
          Facultatif : un jeu de filtres pour exclure des fichiers, et une
          limite de bande passante.
        </li>
        <li>
          En Miroir, réglez les seuils de suppression. Ce sont eux qui
          arrêteront la tâche le jour où quelque chose ira mal.
        </li>
        <li>
          <strong>Simulez</strong>, et lisez ce que la simulation annonce —
          fichier par fichier si besoin, dans l'historique. C'est le moment où
          une erreur de chemin se voit encore gratuitement.
        </li>
        <li>
          <strong>Lancez</strong>, puis <strong>Planifiez</strong> une fois le
          résultat conforme.
        </li>
      </ol>
    </div>
  );
}

export default function HelpView({ bidirectional = false }: Props) {
  return (
    <>
      <section className="card">
        <h2>Modes et suppressions</h2>
        <AideModes bidirectional={bidirectional} />
      </section>

      <section className="card">
        <h2>Ce qui protège vos données</h2>
        <AideSuppressions />
      </section>

      <section className="card">
        <h2>Ajouter un stockage Cloud</h2>
        <AideStockage />
      </section>

      <section className="card">
        <h2>Créer une tâche de synchronisation</h2>
        <AideTache />
      </section>

      <section className="card">
        <h2>Comprendre un statut</h2>
        <div className="aide">
          <dl className="aide__liste">
            <dt>Réussie</dt>
            <dd>Tout est passé, sans erreur.</dd>

            <dt>Avertissement</dt>
            <dd>
              L'exécution est allée à son terme, mais des fichiers ont échoué
              individuellement. L'historique nomme lesquels et pourquoi.
            </dd>

            <dt>Bloquée — validation nécessaire</dt>
            <dd>
              Les suppressions mesurées dépassent le seuil de la tâche. Rien
              n'a été touché. Examinez la liste, puis confirmez ou corrigez la
              tâche.
            </dd>

            <dt>Interrompue</dt>
            <dd>
              L'exécution a été arrêtée, ou le conteneur s'est éteint pendant
              qu'elle tournait. Jamais présentée comme une réussite, même si
              une partie des fichiers est bien passée.
            </dd>

            <dt>Ré-initialisation nécessaire</dt>
            <dd>
              Propre au Bidirectionnel : la référence des deux côtés est
              absente ou inutilisable, et il faut la rétablir explicitement.
            </dd>
          </dl>
        </div>
      </section>
    </>
  );
}
