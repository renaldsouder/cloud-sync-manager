# Cloud Sync Manager — contexte du développement

> Document destiné à la base de connaissances d'un projet Claude. Il rassemble
> ce qu'une conversation neuve doit savoir pour être utile sur ce projet sans
> avoir le code sous la main.
>
> À jour au **15 octobre 2026**, version **0.3.0** (tag `v0.3.0`).

---

## 1. Ce qu'est le produit

Une application Docker pour **Unraid**, destinée au catalogue *Community
Applications*, qui offre l'équivalent de **Synology Cloud Sync** : synchroniser
des dossiers du serveur avec un stockage Cloud, depuis une interface Web, sans
jamais ouvrir un terminal.

Le moteur est **rclone**, piloté en processus enfant. L'application n'invente
aucun protocole Cloud : elle apporte l'interface, la planification, et surtout
les garde-fous qui manquent à un `rclone sync` lancé à la main.

**Un seul conteneur**, un seul port (3572), toute la persistance dans l'appdata
Unraid.

L'auteur est **Renald**, qui développe sous Windows et déploie sur son propre
Unraid. L'interface est **en français uniquement**.

---

## 2. Le vocabulaire — c'est un contrat, pas du style

Trois modes, nommés dans l'interface et dans toute la documentation. Employer
un autre mot fait chercher un bouton qui n'existe pas.

| Mode produit | rclone | Supprime |
|---|---|---|
| **Copie** | `copy` | jamais |
| **Miroir** | `sync` | à la destination, ce qui n'existe plus à la source |
| **Bidirectionnel** | `bisync` | des deux côtés |

Le **sens** est distinct du mode : **Local → Cloud** ou **Cloud → Local**. Jamais
« upload » ni « download ». Le sens décide quel côté peut perdre des fichiers ;
le mode décide s'il en a le droit.

Ne jamais appeler « sauvegarde » ce qui n'offre pas les garanties d'une
sauvegarde versionnée.

---

## 3. Les règles non négociables

Elles viennent du cahier des charges (§8, §18, §28) et priment sur toute
considération de confort.

- **Fail closed** — source inaccessible, anormalement vide, ou listing non
  fiable ⇒ échec **sans propager de suppression**. Le scénario redouté est le
  share démonté : la source répond « vide », et un miroir naïf viderait le Cloud.
- **Simulation avant destruction** — obligatoire à la première exécution d'un
  Miroir, après tout changement de source, de destination ou de sens, après
  activation de la propagation des suppressions, et avant une initialisation
  bidirectionnelle.
- **Seuil de suppression** configurable, en nombre et/ou en pourcentage.
  Dépassement ⇒ statut `Bloqué — validation nécessaire`, **rien n'est touché**,
  et les fichiers concernés sont nommés.
- **Suppression distante jamais activée par défaut.**
- Après arrêt forcé ou plantage : `Interrupted`, **jamais** `Success`.
- **Secrets** — jamais en clair dans les logs, l'API de lecture, un export ou un
  package de diagnostic. L'interface affiche « configuré », rien d'autre.
- **rclone** — toujours `subprocess` avec **liste d'arguments**, jamais
  `shell=True`, jamais de concaténation shell. Aucun mot de passe dans `argv` :
  les phrases de passe passent par `stdin` ou l'environnement.
- **Chemins** — validation stricte, `..` interdit, aucune évasion hors des
  racines montées.
- Jamais `--privileged`, jamais `/var/run/docker.sock`.
- **Aucune optimisation de confort, de vitesse ou de simplicité ne justifie de
  réduire les protections contre la perte de données.**

Le critère de réussite du projet (§22) n'est pas le nombre de fournisseurs
supportés : c'est **zéro perte de données silencieuse**.

---

## 4. État au 15 octobre 2026 — v0.3.0

**412 tests** passent sous Linux (411 sous Windows : un test de lien symbolique
y est sauté). CI verte sur les quatre jobs : tests backend, compilation
frontend, validation du template Unraid, image Docker.

Livré et fonctionnel : Copie et Miroir dans les deux sens avec toutes les
protections destructives ; planification persistante ; filtres avec outil de
test ; notifications Unraid et webhook ; export/restauration de configuration ;
historique fichier par fichier ; trois niveaux de journalisation ; explorateurs
de dossiers local et distant ; création, modification, duplication et
suppression protégée des tâches ; assistant de première utilisation ; aide
intégrée ; recherche et tri des tâches ; **bidirectionnel ouvert**.

### La discipline de maturité

Le projet distingue soigneusement trois choses, et cette distinction se retrouve
dans le README, le template Unraid et l'interface elle-même :

- **délibérément absent** — hors périmètre, assumé ;
- **pas encore fait** — au backlog ;
- **pas encore prouvé** — le code existe et les tests passent, mais rien ne l'a
  confronté au réel.

La troisième catégorie est la plus importante, et la plus facile à taire.

**Ce qui n'est pas prouvé, au 15 octobre 2026 :**

| | |
|---|---|
| Garde-fous destructifs | Testés automatiquement **et** en intégration — mais contre un dossier local tenant lieu de Cloud |
| Fournisseur éprouvé avec un vrai compte | **OneDrive uniquement** |
| Google Drive, Dropbox | Parcours OAuth testé jusqu'à la page de consentement, jamais mené au bout |
| S3, B2, WebDAV, SFTP | Implémentés, jamais confrontés à un vrai service |
| **Bidirectionnel** | Matrice destructive couverte, mais **aucun fournisseur réel validé pour ce mode** |
| Notifications Unraid | Validées sur un vrai Unraid 7.3.2 |

Un dossier local ne sait pas reproduire ce qu'un Cloud fait mal : listings non
fiables, limitation de débit, cohérence différée, granularité des dates de
modification, casse des noms. Ce sont exactement les conditions pour lesquelles
les règles de *fail closed* existent — et elles n'ont pas encore été rencontrées
en vrai.

---

## 5. Stack et dépôts

**Backend** Python 3.13 + FastAPI + SQLAlchemy 2.0 + Alembic + SQLite (WAL).
**Frontend** React 19 + TypeScript + Vite. **rclone 1.75.1** en processus enfant.
SSE pour la progression, planificateur interne persistant, image Docker
multi-stage.

```
backend/src/csm/   config, paths, security/redaction, db/ (+ Alembic),
                   rclone/, services/, api/, main.py
frontend/src/      WebUI React
docker/            Dockerfile multi-stage, entrypoint PUID/PGID/UMASK
unraid/            template XML Community Applications
docs/              cahier des charges (export généré), installation
```

- **Dépôt applicatif** : `github.com/renaldsouder/cloud-sync-manager`
- **Image** : `ghcr.io/renaldsouder/cloud-sync-manager:latest` — `latest` n'est
  publié **que sur un tag `v*`**. Un push sur `main` ne publie qu'une image
  étiquetée par son empreinte : pousser du code ne suffit donc **pas** à mettre
  à jour une installation Unraid, il faut une version taguée.
- **Dépôt de templates** : publié, mais **rien n'est soumis à Community
  Applications** tant que le §22 et des fournisseurs éprouvés ne le justifient pas.
- **Suivi projet** : hors du dépôt applicatif, dans `../_Suivi_Projet/`, outil
  HTML mono-fichier adossé à `cloudsyncmanage.sqlite`.

> ⚠️ `docs/Cloud_Sync_Manager_Cahier_des_charges.md` est un **export généré** par
> l'outil de suivi, pas un fichier à éditer. Ses statuts de backlog sont
> **périmés** : il affiche encore `À faire` pour des exigences livrées depuis
> longtemps. La base du suivi fait foi.

---

## 6. Faits techniques chèrement acquis

Ces points ont coûté des mesures, pas des lectures de documentation. Plusieurs
suppositions raisonnables se sont révélées fausses.

### rclone bisync

- Les options de sauvegarde sont `--backup-dir1` / `--backup-dir2`, **une par
  côté** — pas `--backup-dir`. Le fichier de filtres est `--filters-file`, pas
  `--filter-from`.
- **`--color NEVER` est obligatoire** : bisync colore sa sortie même redirigée,
  y compris à l'intérieur du journal JSON. Sans cela, des séquences d'échappement
  finissent dans les chemins de fichiers enregistrés.
- `--workdir` doit pointer sur un répertoire **persistant**. Son emplacement par
  défaut est un cache utilisateur qui meurt avec le conteneur : chaque
  recréation deviendrait une ré-initialisation forcée.
- **Le code de sortie 7 signifie « lancez `--resync` »**, pas « échec ». Le
  confondre avec une erreur générique prive l'utilisateur de la seule action qui
  débloque la situation.
- `--conflict-resolve none` + `--conflict-loser num` conserve les deux versions
  en `.conflict1` / `.conflict2` — **mais le nom d'origine disparaît des deux
  côtés**. Rien n'est perdu ; l'interface doit le dire, sinon l'utilisateur croit
  son fichier volatilisé.
- bisync **avorte de lui-même** au-delà d'environ 50 % de fichiers modifiés.
- Un fichier `.lck` survit à un plantage et bloque la tâche **pour toujours** ;
  le seul remède documenté par rclone est de le supprimer à la main. L'application
  le lève au démarrage, uniquement pour des exécutions dont elle vient d'établir
  qu'elles sont mortes.
- bisync **rejette `--exclude`** : l'exclusion de la corbeille doit être écrite
  dans le fichier de filtres.

### Classification des événements rclone

- Une ligne de niveau **DEBUG raconte comment rclone s'y prend, jamais ce qu'il a
  fait** — le résultat est annoncé en INFO. Sans cette coupure, le niveau
  diagnostic compte chaque fichier deux fois (la copie passe par un `.partial`
  puis un `renamed to:`) et transforme en erreurs les tentatives que rclone
  réessaie seul. **Les compteurs ne doivent jamais dépendre de la verbosité.**
- Avec une corbeille, rclone ne supprime pas : il **déplace**, et n'émet donc
  jamais « Deleted ». Sans traitement de « Moved into backup dir », aucune
  suppression ne serait tracée dès que la corbeille est active.
- bisync annonce son abandon en **NOTICE**, pas en ERROR.

### Seuils de suppression

- `max_deletes = 0` signifie **« bloquer dès la première suppression »**, pas
  « désactivé ». Il n'existe volontairement aucune valeur « sans limite ».
- Le seuil en pourcentage ne s'applique **qu'au-delà de 5 suppressions** : sur
  une poignée de fichiers, un pourcentage ne veut rien dire, et un garde-fou qui
  se déclenche sans arrêt s'apprend à être contourné.
- Défauts : `local_to_remote` 100 / 10 %, `remote_to_local` 25 / 5 % — plus
  stricts vers le serveur, dont les données ne seront reconstruites par personne.

### Corbeille

`.cloudsync-trash/<horodatage>` **dans** la destination, exclue de la
synchronisation. Placée dedans et non à côté, parce que c'est la seule forme qui
fonctionne quand la destination est une racine. Conservée 30 jours, et les trois
dernières quoi qu'il arrive.

### Environnement

- La CI est **le seul endroit où le projet est éprouvé sous Linux** — le
  développement se fait sous Windows et la cible est un conteneur. Les runners
  sont épinglés sur `ubuntu-24.04` plutôt que sur l'étiquette mouvante.
- Sous Windows, `localhost` résout en IPv6 d'abord, et un conteneur Docker
  Desktop peut masquer un port : utiliser `127.0.0.1`.
- Reproduire la CI localement se fait dans un conteneur `python:3.13-slim`, avec
  `MSYS_NO_PATHCONV=1`.

---

## 7. Conventions de travail

- **Commits** en français, titrés par l'identifiant de backlog : `LOG-003 — trois
  niveaux de journalisation`. Le corps explique le *pourquoi* et avoue les
  défauts trouvés en chemin.
- **Tests** nommés en anglais, docstrings en français expliquant **ce qui serait
  perdu si le test disparaissait**. Une fonction destructive n'est jamais
  terminée sans test automatisé **et** test d'intégration.
- Les commits peuvent être faits sans demander. **`git push` et toute
  publication demandent une confirmation explicite.**
- Les commandes proposées à l'utilisateur doivent être en **PowerShell**
  (`Select-String`, pas `grep`).
- Les codes d'autorisation OAuth et les jetons sont des identifiants : ils n'ont
  leur place que dans l'application, jamais dans une conversation ni un journal.
- **Mesurer avant d'affirmer.** Cette habitude a renversé à elle seule plusieurs
  suppositions raisonnables sur ce projet : les options de bisync, le contenu
  réel du journal rclone, et la méthode d'installation d'un template Unraid.
- Retours **courts**, et proposer la suite à chaque fin de tâche.

### Un piège d'environnement récurrent

Les références de suivi git (`.git/refs/remotes`, `.git/logs/refs/remotes`) ont
appartenu au groupe Administrateurs après un `git` lancé une fois en
administrateur. Conséquence : `git push` réussissait **en affichant une erreur**,
et `origin/main` restait figé. Réparé le 14 octobre 2026 en supprimant les deux
arborescences depuis une fenêtre élevée, puis en relançant `git fetch` depuis une
fenêtre **normale** — c'est cette seconde partie qui comptait : refaire le fetch
en administrateur recrée exactement le problème.

---

## 8. Ce qui reste ouvert

1. **Le bidirectionnel face à un vrai fournisseur.** C'est le seul trou qui ne
   peut pas être comblé par du code : un Cloud ne se comporte pas comme un disque
   sur les dates de modification et la casse des noms, dont bisync dépend
   entièrement pour décider quel côté a raison. Premier essai prévu sur OneDrive,
   **sur un dossier de test**.
2. **Les fournisseurs autres qu'OneDrive**, jamais menés au bout avec un vrai
   compte.
3. **Le §22.** Le critère demande 30 jours d'utilisation réelle sur Unraid sans
   suppression inattendue. Le compteur a démarré le 9 septembre 2026, mais
   l'application a été profondément modifiée depuis : la question de savoir si
   ces 30 jours sont acquis, ou s'ils redémarrent avec la 0.3.0, mérite d'être
   tranchée explicitement plutôt que laissée au calendrier.
4. **Avant toute soumission à Community Applications** : ouvrir un fil de support
   sur le forum Unraid, puis remplacer l'URL de support dans les deux XML.
5. **Chiffrement côté application (SEC-005)** — reporté, et c'est délibéré :
   rclone sait déjà chiffrer, et une couche maison mal éprouvée serait pire que
   pas de couche du tout.
6. Confort restant : niveaux de détail supplémentaires, assistants simplifiés par
   fournisseur, interface en anglais.
