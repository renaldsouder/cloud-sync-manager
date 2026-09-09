# CLAUDE.md — Application Cloud Sync pour Unraid

> **Nom de travail** : Unraid Cloud Sync  
> **Nom définitif** : à définir avant publication Community Applications  
> **Responsable produit** : Renald  
> **Version du cahier des charges** : 1.0  
> **Cible initiale** : MVP `0.1.0`, puis `1.0.0`  
> **Dernière mise à jour** : 08/09/2026

---

## 0. Rôle de ce fichier

Ce fichier est la **source de vérité de développement pour Claude Code**. Il décrit la vision produit, l'architecture, les règles non négociables, le cahier des charges, les priorités, les critères d'acceptation et le backlog fonctionnel.

Claude Code doit :

- lire ce fichier avant toute modification significative ;
- conserver les identifiants du backlog (`SYNC-001`, `SEC-004`, etc.) ;
- traiter en priorité `P0`, puis `P1`, puis `P2`, puis `P3` ;
- ne jamais supprimer silencieusement une exigence : une exigence abandonnée reste tracée avec un statut explicite ;
- analyser l'impact avant de modifier plusieurs couches à la fois ;
- privilégier les changements ciblés aux réécritures complètes de fichiers ;
- lire uniquement les fichiers utiles à la tâche afin de limiter le bruit et la consommation de contexte ;
- exécuter les tests pertinents après chaque modification ;
- expliquer brièvement les choix techniques importants dans le code ou la documentation lorsqu'ils ne sont pas évidents ;
- ne jamais considérer une fonctionnalité destructive comme terminée sans test automatisé et test d'intégration ;
- mettre à jour ce cahier des charges lorsqu'une décision d'architecture ou de comportement devient définitive.

### Statuts autorisés

`À faire` · `En cours` · `À tester` · `Validé` · `Disponible` · `Bug` · `Bloqué` · `Demande` · `Reporté`

### Types d'entrées

`FONC` · `UI` · `BUG` · `CORR` · `QUALITE` · `SECURITE` · `DECISION` · `QUESTION` · `EVO` · `DETTE`

---

# 1. Vision produit

## 1.1 Problème

Unraid sait très bien stocker les données, mais l'utilisateur qui souhaite synchroniser simplement ses shares avec un ou plusieurs services Cloud doit aujourd'hui assembler des outils, comprendre des commandes ou installer des interfaces génériques qui ne sont pas pensées spécifiquement pour l'expérience Unraid.

L'objectif est de créer une application installable depuis **Unraid Community Applications** qui offre une expérience proche de **Synology Cloud Sync** : choisir un stockage Cloud, choisir un dossier local, choisir le sens de synchronisation, planifier et laisser fonctionner.

## 1.2 Formule produit

**La synchronisation doit être invisible lorsqu'elle fonctionne, mais totalement explicite dès qu'elle peut supprimer, écraser ou déplacer des données.**

## 1.3 Double mandat non négociable

Le produit doit être simultanément :

1. **simple pour un utilisateur Unraid non spécialiste de rclone** ;
2. **suffisamment puissant et transparent pour un utilisateur avancé** qui veut comprendre les opérations, les filtres, les conflits, les performances et les erreurs.

Ce n'est pas une simple interface graphique posée au-dessus d'une ligne de commande. La valeur produit est dans la **sécurité des choix, la lisibilité, l'orchestration, le diagnostic et l'intégration Unraid**.

## 1.4 Principes fondateurs

- **Sécurité avant automatisation** : aucune suppression risquée ne doit être silencieuse.
- **Simulation avant destruction** : la première exécution d'une configuration destructive doit être simulée.
- **Fail closed** : en cas de doute sur la disponibilité d'une source, on bloque les suppressions.
- **Provider agnostic** : l'application s'appuie sur un moteur multi-fournisseurs plutôt que réimplémenter chaque API Cloud.
- **Local first** : aucune donnée applicative ou télémétrie ne quitte le serveur sans action ou configuration explicite.
- **Aucune dépendance au terminal** dans le parcours normal.
- **Unraid natif dans l'expérience** : Docker, appdata, shares, WebUI et Community Applications.
- **Traçabilité complète** : chaque exécution est historisée.

---

# 2. Utilisateurs cibles

## 2.1 Particulier / homelab

Utilisateur Unraid qui souhaite synchroniser photos, documents, sauvegardes ou médias avec Google Drive, OneDrive, Dropbox, S3, B2, WebDAV ou SFTP sans écrire de commandes.

Attentes : installation simple, assistant, planification, notifications en cas d'erreur et aucune mauvaise surprise sur les suppressions.

## 2.2 Utilisateur avancé

Utilisateur qui connaît Docker, les shares et parfois rclone, mais souhaite centraliser plusieurs tâches avec historique, filtres, limitations de bande passante, diagnostics et paramètres avancés.

## 2.3 TPE / PME

Petit environnement professionnel utilisant Unraid comme stockage et souhaitant externaliser ou synchroniser certains dossiers avec des services Cloud sans déployer une plateforme complexe.

Attentes supplémentaires : fiabilité, logs, export de configuration, reprise après incident et comportement prévisible.

---

# 3. Parcours utilisateur de référence

1. Installer l'application depuis Community Applications.
2. Ouvrir la WebUI.
3. L'assistant de première utilisation propose d'ajouter un stockage distant.
4. L'utilisateur choisit le fournisseur et réalise l'authentification.
5. L'application teste le stockage distant et affiche clairement le résultat.
6. L'utilisateur choisit un dossier/share local accessible au conteneur.
7. Il choisit un dossier distant.
8. Il choisit le mode : **Copie**, **Miroir** ou, plus tard, **Bidirectionnel**.
9. L'interface explique les conséquences sur les suppressions.
10. Une simulation est exécutée et présente les fichiers à créer, modifier et supprimer.
11. L'utilisateur valide la tâche.
12. Il choisit une planification ou un lancement manuel.
13. Le tableau de bord affiche état, progression, dernier résultat et prochaine exécution.
14. En cas d'erreur, l'utilisateur accède au diagnostic sans ouvrir les logs Docker.

---

# 4. Périmètre des versions

## 4.1 MVP — 0.1.x

Le MVP doit démontrer une chaîne complète, sûre et exploitable :

- installation Docker sur Unraid ;
- WebUI ;
- configuration persistante dans appdata ;
- création et test d'un remote rclone ;
- sélection d'un chemin local et distant ;
- tâche unidirectionnelle **Local → Cloud** ;
- mode **Copie** sans suppression distante ;
- mode **Miroir** avec avertissements et simulation obligatoire ;
- lancement manuel ;
- suivi d'exécution ;
- logs et historique ;
- arrêt d'une tâche ;
- masquage des secrets ;
- garde-fous contre une source inaccessible ou vide de manière anormale.

## 4.2 Version 1.0

- Cloud → Local ;
- assistants fournisseurs simplifiés ;
- planification complète ;
- filtres ;
- notifications ;
- limitations de bande passante ;
- sauvegarde/restauration de configuration ;
- Community Applications prêt à publier ;
- interface responsive ;
- matrice de compatibilité par fournisseur ;
- bidirectionnel uniquement si la suite de sécurité est validée.

## 4.3 Roadmap

- chiffrement client assisté ;
- webhooks avancés ;
- canaux Stable/Beta ;
- fonctions avancées de versioning ;
- opérations ponctuelles supplémentaires ;
- intégrations domotiques/monitoring plus poussées.

---

# 5. Architecture cible

## 5.1 Décision générale

**Un seul conteneur Docker** pour l'expérience Unraid standard. Pas de dépendance obligatoire à Docker Compose.

Architecture logique :

```text
Navigateur
   │
   ▼
Frontend React + TypeScript
   │ HTTP / SSE
   ▼
API FastAPI (Python)
   ├── Gestion configuration / SQLite
   ├── Planificateur
   ├── Job runner
   ├── Adaptateur rclone
   └── Journal / notifications
            │
            ▼
         rclone
      ┌─────┴─────────┐
      ▼               ▼
Shares Unraid      Cloud / SFTP / WebDAV / S3...
```

## 5.2 Stack retenue pour le MVP

- **Backend** : Python 3.13 + FastAPI.
- **Frontend** : React + TypeScript + Vite.
- **Persistance métier** : SQLite.
- **Moteur de transfert** : rclone exécuté comme processus enfant contrôlé.
- **Progression temps réel** : SSE en priorité ; WebSocket uniquement si un besoin bidirectionnel temps réel apparaît.
- **Planification** : planificateur interne persistant, simple à tester ; ne pas exposer cron comme interface principale.
- **Packaging** : image Docker multi-stage.

Cette stack est une décision de travail. Toute proposition de changement doit démontrer un gain réel en sécurité, maintenance ou simplicité de déploiement avant modification.

## 5.3 Règles d'isolation

- ne jamais exiger `--privileged` ;
- ne jamais monter `/var/run/docker.sock` ;
- ne pas exécuter rclone avec plus de droits que l'application ;
- supporter `PUID`, `PGID` et `UMASK` ou un mécanisme équivalent adapté à Unraid ;
- n'accéder qu'aux chemins montés dans le conteneur ;
- ne jamais tenter d'explorer le système hôte hors des mappings autorisés.

## 5.4 Accès aux shares

Deux modes doivent être compatibles :

1. **Mode simple** : l'administrateur monte `/mnt/user` dans le conteneur pour permettre la sélection de plusieurs shares.
2. **Mode restrictif** : l'administrateur monte seulement un ou plusieurs shares spécifiques.

L'application ne doit jamais supposer que `/mnt/diskX` est accessible ni contourner les User Shares d'Unraid par défaut.

---

# 6. Modèle de données

SQLite contient les données applicatives, mais **pas les secrets Cloud en clair**.

Tables logiques minimales :

### `remotes`

- `id` stable
- `name`
- `provider`
- `rclone_remote_name`
- `status`
- `last_test_at`
- `capabilities_json`
- `created_at` / `updated_at`

### `tasks`

- `id` stable
- `name`
- `remote_id`
- `local_path`
- `remote_path`
- `direction`
- `mode` (`copy`, `mirror`, `bisync`)
- `delete_policy`
- `filter_set_id`
- `schedule_json`
- `bandwidth_json`
- `enabled`
- `last_run_id`
- `created_at` / `updated_at`

### `task_runs`

- `id`
- `task_id`
- `started_at` / `ended_at`
- `status`
- `exit_code`
- `transferred_files`
- `transferred_bytes`
- `deleted_files`
- `errors_count`
- `dry_run`
- `rclone_version`
- `summary_json`

### `task_events`

Événements structurés d'une exécution : transfert, suppression, erreur, retry, conflit, avertissement.

### `filter_sets`

Règles d'inclusion/exclusion réutilisables.

### `settings`

Paramètres généraux, rétention, UI, notifications et comportement.

## 6.1 Authentification rclone

Le fichier de configuration rclone est stocké dans l'appdata avec des permissions strictes. Ne jamais présenter `rclone obscure` comme un chiffrement fort : c'est une obfuscation, pas une protection cryptographique autonome.

Les secrets, tokens et mots de passe :

- ne doivent jamais apparaître dans les logs ;
- ne doivent jamais être renvoyés par l'API une fois enregistrés ;
- ne doivent jamais être inclus dans un package de diagnostic ;
- doivent être remplacés dans l'UI par un statut du type « configuré ».

---

# 7. Sémantique des modes de synchronisation

Le vocabulaire de l'UI est un contrat produit. Ne jamais appeler « sauvegarde » une opération qui n'offre pas réellement les garanties d'une sauvegarde versionnée.

## 7.1 Copie

**Source → Destination, sans suppression à destination.**

Usage : envoyer les nouveaux fichiers et mises à jour vers le Cloud sans supprimer les éléments déjà présents à destination.

Implémentation cible : comportement équivalent à `rclone copy`.

## 7.2 Miroir

**La destination devient le reflet de la source.**

Les éléments absents de la source peuvent être supprimés à destination.

Implémentation cible : comportement équivalent à `rclone sync`.

Ce mode est **destructif** et impose les protections du chapitre 8.

## 7.3 Bidirectionnel

Les modifications des deux côtés sont comparées et propagées.

Implémentation cible : `rclone bisync`, mais cette fonction est **hors MVP** et ne doit être activée en 1.0 qu'après validation de la matrice de tests et des garde-fous.

Ne jamais lancer automatiquement une initialisation ou un `resync` bidirectionnel sans action explicite de l'utilisateur.

---

# 8. Sécurité fonctionnelle — règles non négociables

## 8.1 Simulation obligatoire

Une simulation est obligatoire :

- à la première exécution d'un mode Miroir ;
- après changement de source ;
- après changement de destination ;
- après inversion du sens ;
- après activation de la propagation des suppressions ;
- avant l'initialisation bidirectionnelle.

## 8.2 Protection contre source inaccessible

Si la source attendue n'est plus montée, n'est plus accessible ou retourne un état incohérent, **la tâche échoue sans propager de suppression**.

Exemples à considérer comme dangereux :

- share Unraid absent ;
- chemin monté vide alors qu'il contenait précédemment des données ;
- remote Cloud inaccessible ;
- erreur d'authentification ;
- timeout massif ;
- quota ou permission empêchant le listing fiable.

## 8.3 Seuil de suppression

Prévoir un garde-fou configurable :

- nombre maximal de suppressions ;
- et/ou pourcentage maximal de la destination supprimable en une exécution.

Si le seuil est dépassé, la tâche passe en **Bloqué — validation nécessaire**.

## 8.4 Historique destructif

Toute suppression doit laisser une trace structurée dans `task_events`.

## 8.5 Arrêt

Un bouton **Arrêter** doit interrompre proprement le processus rclone. Après arrêt forcé ou crash, l'exécution est marquée `Interrupted`, jamais `Success`.

## 8.6 Bidirectionnel

Pour le bidirectionnel :

- prévoir un mécanisme de vérification d'accès aux deux côtés ;
- tester les conflits ;
- tester la perte de connexion ;
- tester les renommages ;
- tester les suppressions croisées ;
- tester les différences de casse et Unicode ;
- tester les limites propres au fournisseur ;
- ne jamais considérer la compatibilité d'un backend comme acquise uniquement parce que rclone sait s'y connecter.

---

# 9. Fournisseurs Cloud

## 9.1 Fournisseurs à présenter en priorité dans l'UI

- Google Drive
- Microsoft OneDrive
- Dropbox
- Amazon S3 et compatibles S3
- Backblaze B2
- WebDAV
- SFTP

## 9.2 Mode avancé

Les autres backends rclone peuvent être exposés via un mode avancé lorsque le mécanisme générique est suffisamment fiable.

## 9.3 Matrice de capacités

Chaque fournisseur doit avoir une matrice de capacités détectées ou testées :

- authentification disponible ;
- listing ;
- upload ;
- download ;
- checksum disponible ;
- date de modification fiable ;
- renommage serveur ;
- suppression ;
- dossier vide ;
- caractères spéciaux ;
- mode bidirectionnel certifié par nos tests.

Une connexion réussie ne signifie pas que toutes les fonctions sont supportées.

---

# 10. Interface utilisateur

## 10.1 Navigation principale

1. **Tableau de bord**
2. **Tâches**
3. **Stockages Cloud**
4. **Historique**
5. **Paramètres**
6. **Aide / Diagnostic**

## 10.2 Tableau de bord

Afficher sans ouvrir une tâche :

- nom ;
- sens ;
- source → destination ;
- état ;
- progression si active ;
- dernier résultat ;
- dernière exécution ;
- prochaine exécution ;
- volume transféré ;
- avertissement éventuel.

États visuels minimum :

`Prête` · `Planifiée` · `En cours` · `En pause` · `Réussie` · `Avertissement` · `Erreur` · `Bloquée`

## 10.3 Assistant de tâche

Étapes :

1. Nom
2. Stockage Cloud
3. Source locale/distante
4. Destination
5. Sens et mode
6. Suppressions et sécurité
7. Filtres
8. Planification
9. Performances
10. Simulation
11. Validation

## 10.4 Règles UX

- une option destructive ne doit jamais être présentée comme un simple toggle sans explication ;
- les boutons critiques doivent nommer l'action réelle : « Supprimer 423 fichiers » plutôt que « Confirmer » ;
- les chemins source et destination restent visibles sur l'écran de confirmation ;
- l'utilisateur doit pouvoir revenir modifier un réglage sans perdre toute la tâche en cours de création ;
- les erreurs doivent proposer une cause probable et une action possible ;
- ne jamais obliger l'utilisateur à lire la sortie brute de rclone pour comprendre l'état de base ;
- responsive desktop/tablette/mobile, avec priorité au desktop Unraid.

---

# 11. Planification

Le parcours standard ne doit pas demander d'écrire une expression cron.

Options attendues :

- manuel ;
- toutes les N minutes ;
- toutes les N heures ;
- tous les jours à une heure donnée ;
- jours de semaine sélectionnés ;
- plages horaires ;
- option avancée cron en Roadmap.

Le planificateur doit survivre au redémarrage du conteneur.

Au démarrage, une tâche manquée ne doit pas se lancer automatiquement si cela risque de créer plusieurs exécutions concurrentes. La politique de rattrapage doit être explicite.

---

# 12. Filtres

Support minimum V1 :

- inclusion par chemin ;
- exclusion par chemin ;
- extensions ;
- motifs de nom ;
- fichiers cachés ;
- taille minimale/maximale ;
- test de filtre avant enregistrement.

Le test doit permettre de comprendre **pourquoi** un fichier est inclus ou exclu.

---

# 13. Performances

Paramètres V1 :

- transferts simultanés ;
- vérifications simultanées ;
- bande passante upload/download ;
- limitation horaire en Roadmap.

Objectifs applicatifs :

- WebUI disponible moins de 5 secondes après le démarrage normal du conteneur ;
- tableau de bord utilisable en moins de 1 seconde avec 100 tâches et un historique raisonnable ;
- aucun blocage de l'API pendant un transfert long ;
- pagination/virtualisation des historiques volumineux ;
- rétention des logs configurable ;
- aucune croissance illimitée de la base SQLite.

Les performances de transfert sont celles du fournisseur, du réseau, du stockage et de rclone ; l'application ne doit pas prétendre garantir un débit absolu.

---

# 14. Logs, historique et diagnostic

Chaque exécution conserve :

- début / fin ;
- durée ;
- résultat ;
- code retour ;
- fichiers transférés ;
- octets transférés ;
- suppressions ;
- erreurs ;
- retries ;
- version rclone ;
- configuration non secrète utile au diagnostic.

### Niveaux

- **Normal** : résumé exploitable par l'utilisateur.
- **Détaillé** : événements fichier par fichier.
- **Diagnostic** : détails techniques temporaires.

Le diagnostic doit appliquer un filtre de secrets centralisé avant toute écriture ou export.

---

# 15. Notifications

V1 :

- échec de tâche ;
- tâche bloquée ;
- authentification expirée ;
- seuil de suppression atteint ;
- succès optionnel.

Canaux :

- notification Unraid lorsque l'intégration est disponible ;
- webhook générique en Roadmap/V1.x.

---

# 16. Configuration, sauvegarde et restauration

Tout ce qui doit survivre au conteneur est sous l'appdata.

Le projet doit permettre :

- export de la configuration applicative ;
- import/restauration ;
- contrôle de version du format ;
- migrations additives et testées ;
- sauvegarde recommandée avant migration importante.

Une restauration ne doit pas lancer automatiquement les tâches importées avant validation des chemins et remotes.

---

# 17. Intégration Unraid

Le livrable doit comprendre :

- image Docker versionnée ;
- `Dockerfile` ;
- `.dockerignore` ;
- template XML Unraid ;
- icône ;
- lien WebUI ;
- port configurable ;
- appdata configurable ;
- variables nécessaires ;
- mappings de volumes documentés ;
- dépôt source ;
- documentation de support ;
- notes de version.

Les paramètres d'une application Unraid étant sauvegardés dans des templates Docker XML, toute évolution des variables ou mappings doit préserver autant que possible la compatibilité avec les installations existantes.

---

# 18. Sécurité technique

Exigences non négociables :

- aucun secret dans Git ;
- aucun secret dans les logs ;
- aucun token renvoyé par l'API de lecture ;
- validation stricte des chemins locaux ;
- empêcher `..` et toute évasion hors des racines montées ;
- validation/échappement de tous les arguments passés à rclone ;
- ne jamais construire une commande rclone par concaténation shell non contrôlée ;
- utiliser une liste d'arguments avec `subprocess` sans `shell=True` ;
- authentifier ou protéger la WebUI lorsqu'elle est exposée hors LAN ;
- documenter le reverse proxy HTTPS ;
- en-têtes HTTP de sécurité ;
- dépendances régulièrement mises à jour ;
- scan des dépendances et de l'image Docker avant release lorsque possible.

---

# 19. Critères de qualité

Une fonction n'est pas `Disponible` parce que l'UI existe.

Pour passer à `Disponible`, il faut :

1. code fonctionnel ;
2. test unitaire lorsque pertinent ;
3. test d'intégration lorsque le comportement touche rclone, SQLite ou le filesystem ;
4. gestion d'erreur ;
5. aucune fuite de secret ;
6. état UI cohérent ;
7. comportement documenté ;
8. mise à jour du backlog ;
9. aucune régression P0/P1 connue.

---

# 20. Stratégie de tests

## 20.1 Tests unitaires

- validation de chemins ;
- règles de planification ;
- filtres ;
- seuils de suppression ;
- parsing de progression rclone ;
- redaction des secrets ;
- transitions d'état des tâches.

## 20.2 Tests d'intégration locaux

Utiliser deux dossiers temporaires ou des backends rclone locaux pour tester :

- copie initiale ;
- mise à jour ;
- renommage ;
- suppression ;
- fichiers identiques ;
- fichiers avec dates différentes ;
- fichiers avec caractères spéciaux ;
- Unicode ;
- gros fichier ;
- grand nombre de petits fichiers ;
- interruption ;
- relance ;
- source absente ;
- destination absente.

## 20.3 Tests destructifs obligatoires

Avant toute release contenant Miroir ou Bidirectionnel :

- source devenue vide ;
- share démonté ;
- remote inaccessible ;
- 1 fichier supprimé ;
- 10 % supprimé ;
- 100 % supprimé ;
- dépassement du seuil configuré ;
- dry-run cohérent avec l'exécution réelle ;
- arrêt pendant suppression ;
- crash du conteneur ;
- redémarrage après exécution interrompue.

## 20.4 Tests fournisseurs

Créer une suite de validation par fournisseur. Une fonctionnalité n'est marquée compatible que si elle a été réellement testée sur ce fournisseur.

---

# 21. Hors périmètre V1

Sujets documentés afin d'éviter l'extension incontrôlée du périmètre :

- moteur de sauvegarde versionnée complet ;
- remplacement de rclone par des connecteurs Cloud propriétaires développés un par un ;
- montage FUSE des Clouds comme filesystem général ;
- application mobile native ;
- SaaS multi-tenant ;
- gestion d'utilisateurs/équipes avancée ;
- réplication distribuée entre plusieurs nœuds ;
- Kubernetes ;
- support natif d'autres NAS que la distribution Docker générique ;
- interface complète d'administration rclone indépendante des tâches Cloud Sync ;
- déduplication globale ;
- chiffrement serveur propriétaire.

Le bidirectionnel n'est pas hors périmètre V1, mais il est **conditionné par la validation de sécurité**. S'il n'est pas suffisamment fiable, il est reporté plutôt que livré avec un risque de perte de données.

---

# 22. Critère de réussite principal

Pendant au moins **30 jours d'utilisation réelle sur Unraid**, les tâches planifiées doivent pouvoir fonctionner sans ligne de commande et sans suppression inattendue, et toute erreur importante doit être compréhensible depuis la WebUI.

Le critère le plus important n'est pas le nombre de fournisseurs supportés : c'est **zéro perte de données silencieuse**.

---

# 23. Plan de construction recommandé

## J1 — Fondations

- structure monorepo ;
- Docker ;
- FastAPI ;
- React ;
- SQLite ;
- healthcheck ;
- configuration appdata ;
- tests de base.

## J2 — Remotes

- adaptateur rclone ;
- version rclone ;
- création/test de remote ;
- stockage des métadonnées ;
- masquage des secrets ;
- premier assistant.

## J3 — Tâches unidirectionnelles

- modèle Task ;
- Local → Cloud ;
- Copy ;
- dry-run ;
- lancement manuel ;
- progression ;
- arrêt.

## J4 — Miroir et sécurité

- Sync/Miroir ;
- détections source inaccessible ;
- seuil de suppression ;
- confirmation renforcée ;
- tests destructifs.

## J5 — Planification et historique

- scheduler ;
- task_runs ;
- task_events ;
- tableau de bord ;
- historique ;
- rétention.

## J6 — V1 Unraid

- Cloud → Local ;
- filtres ;
- limites de bande passante ;
- notifications ;
- sauvegarde/restauration ;
- template Community Applications ;
- documentation.

## J7 — Bidirectionnel

Ne commencer qu'après stabilité des modes unidirectionnels.

- protocole d'initialisation ;
- simulation ;
- conflits ;
- check-access ;
- providers certifiés ;
- suite destructive complète ;
- UX de resync explicite.

---

# 24. Fonctions produit — MoSCoW

| Priorité | Fonction | Description |
|---|---|---|
| Must | Installation Unraid | Installer et démarrer comme un conteneur Unraid standard. |
| Must | WebUI | Configurer et superviser sans terminal. |
| Must | Remote Cloud | Créer, tester et utiliser au moins un remote rclone. |
| Must | Local → Cloud | Synchronisation unidirectionnelle complète. |
| Must | Copy | Mise à jour sans suppression distante. |
| Must | Mirror | Miroir avec protections destructives. |
| Must | Dry-run | Simulation claire avant exécution destructive. |
| Must | Logs / historique | Comprendre chaque exécution. |
| Must | Secrets protégés | Aucun secret exposé dans l'UI ou les logs. |
| Must | Fail closed | Une source inaccessible ne déclenche pas une vague de suppressions. |
| Should | Cloud → Local | Synchronisation inverse. |
| Should | Planification | Exécution périodique et plages horaires. |
| Should | Filtres | Inclusion/exclusion avec test. |
| Should | Notifications | Erreurs et blocages visibles. |
| Should | Bande passante | Limites de transferts. |
| Should | Export/import | Sauvegarder la configuration. |
| Should | Responsive | Utilisation desktop/tablette/mobile. |
| Could | Bidirectionnel | Uniquement si la suite de sécurité est validée. |
| Could | Crypt | Chiffrement client guidé. |
| Could | Webhooks | Intégrations externes. |
| Won't V1 | SaaS multi-tenant | Hors objectif initial. |
| Won't V1 | Moteur de backup complet | Cloud Sync n'est pas un logiciel de sauvegarde versionnée. |

---

# 25. Questions ouvertes

1. Quel sera le **nom définitif** dans Community Applications ?
2. Quels fournisseurs doivent être officiellement mis en avant dès la première version publique ?
3. Le mapping simple `/mnt/user` doit-il être proposé par défaut, ou faut-il privilégier un mapping plus restrictif ?
4. Souhaite-t-on intégrer le chiffrement `crypt` dès la V1 ou après stabilisation de la synchronisation classique ?
5. Quel niveau de notification Unraid est disponible de façon stable depuis un conteneur sans privilège excessif ?
6. Quelle stratégie d'authentification souhaite-t-on pour la WebUI lorsqu'elle est exposée derrière un reverse proxy ?
7. Quels fournisseurs seront déclarés **compatibles bidirectionnels** après tests réels ?
8. Faut-il une politique de quarantaine locale activée par défaut pour les suppressions ?

---

# 26. Backlog détaillé issu du suivi projet

Les identifiants ci-dessous sont stables. Claude Code doit les utiliser dans les commits, notes de version et comptes rendus lorsque pertinent.

## FIRST — Première utilisation

**Périmètre :** Installation depuis Unraid Community Applications, premier lancement, assistant de configuration, création du premier stockage distant et première tâche de synchronisation.

**État initial :** Cahier des charges

**Synthèse :** L’utilisateur doit pouvoir installer l’application puis créer sa première synchronisation en quelques minutes, sans ligne de commande.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| FIRST-001 | FONC | P0 | À faire | Afficher un assistant de première utilisation au premier lancement. | MVP |
| FIRST-002 | FONC | P0 | À faire | Guider l’utilisateur pour créer et tester un premier compte ou stockage Cloud. | MVP |
| FIRST-003 | FONC | P0 | À faire | Guider l’utilisateur pour sélectionner un dossier/share Unraid local et un dossier distant. | MVP |
| FIRST-004 | FONC | P1 | À faire | Proposer une première tâche en mode simulation avant d’autoriser une synchronisation réelle. | MVP |
| FIRST-005 | UI | P2 | Demande | Afficher une checklist de démarrage avec état de chaque étape : Cloud, stockage local, tâche et test. | V1.0 |

## CLOUD — Comptes et stockages Cloud

**Périmètre :** Création, modification, suppression et test des comptes distants utilisés par les tâches de synchronisation.

**État initial :** Cahier des charges

**Synthèse :** Architecture prévue autour d’un moteur compatible avec de nombreux fournisseurs, avec profils simples pour les services les plus courants et configuration avancée pour les autres.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| CLOUD-001 | FONC | P0 | À faire | Créer, modifier, renommer, tester et supprimer un stockage distant. | MVP |
| CLOUD-002 | FONC | P0 | À faire | S’appuyer sur rclone comme moteur de connexion et de transfert multi-Cloud, sous réserve de validation technique. | MVP |
| CLOUD-003 | FONC | P1 | À faire | Prévoir des assistants simplifiés pour Google Drive, Microsoft OneDrive, Dropbox, S3/S3 compatible, Backblaze B2, WebDAV et SFTP. | V1.0 |
| CLOUD-004 | FONC | P2 | Demande | Permettre l’accès aux autres fournisseurs supportés par le moteur via un mode de configuration avancé. | V1.0 |
| CLOUD-005 | FONC | P1 | À faire | Parcourir l’arborescence distante pour sélectionner visuellement le dossier cible ou source. | V1.0 |
| CLOUD-006 | FONC | P2 | Demande | Afficher pour chaque compte son état, dernier test, fournisseur et nombre de tâches associées. | V1.0 |

## LOCAL — Stockages locaux Unraid

**Périmètre :** Sélection des shares, pools, disques ou chemins montés dans le conteneur et contrôle des droits d’accès.

**État initial :** Cahier des charges

**Synthèse :** Le choix du stockage local doit être orienté utilisateur Unraid et éviter la saisie manuelle de chemins dans les cas courants.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| LOCAL-001 | FONC | P0 | À faire | Sélectionner un dossier local parmi les chemins autorisés et montés dans le conteneur. | MVP |
| LOCAL-002 | FONC | P1 | À faire | Présenter clairement les shares Unraid disponibles lorsque le montage du conteneur le permet. | V1.0 |
| LOCAL-003 | QUALITE | P0 | À faire | Vérifier avant lancement les droits lecture/écriture nécessaires sur le chemin local. | MVP |
| LOCAL-004 | FONC | P1 | À faire | Autoriser plusieurs racines locales distinctes pour différentes tâches. | V1.0 |
| LOCAL-005 | QUALITE | P1 | À faire | Empêcher une tâche de sortir des chemins explicitement montés/autorisés dans le conteneur. | MVP |

## SYNC — Moteur de synchronisation

**Périmètre :** Synchronisation entre un stockage local Unraid et un stockage distant, avec plusieurs modes, comparaison, transferts, suppressions et reprise sur erreur.

**État initial :** Cahier des charges

**Synthèse :** Le cœur fonctionnel doit retrouver l’usage d’un Synology Cloud Sync : tâches indépendantes, modes de flux, simulation, état détaillé et exécution fiable.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| SYNC-001 | FONC | P0 | À faire | Créer une synchronisation unidirectionnelle Local vers Cloud. | MVP |
| SYNC-002 | FONC | P0 | À faire | Créer une synchronisation unidirectionnelle Cloud vers Local. | MVP |
| SYNC-003 | FONC | P1 | À faire | Créer une synchronisation bidirectionnelle avec gestion explicite des conflits. | V1.0 |
| SYNC-004 | FONC | P0 | À faire | Prévoir un mode simulation/dry-run indiquant les créations, mises à jour et suppressions sans modifier les fichiers. | MVP |
| SYNC-005 | FONC | P0 | À faire | Comparer les fichiers par taille/date et utiliser les sommes de contrôle lorsqu’elles sont disponibles et pertinentes. | MVP |
| SYNC-006 | QUALITE | P0 | À faire | Reprendre automatiquement une tâche après erreur temporaire avec stratégie de nouvelle tentative et temporisation. | MVP |
| SYNC-007 | FONC | P1 | À faire | Préserver les dates de modification lorsque le fournisseur et le protocole le permettent. | V1.0 |
| SYNC-008 | FONC | P2 | Demande | Permettre une opération ponctuelle de copie sans créer une tâche permanente. | Roadmap |

## TASK — Gestion des tâches

**Périmètre :** Création, duplication, activation, pause, lancement, arrêt, suppression et supervision des tâches de synchronisation.

**État initial :** Cahier des charges

**Synthèse :** Chaque synchronisation doit être une tâche indépendante et facilement pilotable depuis le tableau de bord.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| TASK-001 | FONC | P0 | À faire | Créer et modifier une tâche avec nom, stockage local, stockage distant, sens et options. | MVP |
| TASK-002 | FONC | P0 | À faire | Démarrer, arrêter, mettre en pause et réactiver une tâche. | MVP |
| TASK-003 | FONC | P1 | À faire | Dupliquer une tâche existante pour accélérer la création de configurations proches. | V1.0 |
| TASK-004 | FONC | P1 | À faire | Afficher le dernier lancement, la prochaine exécution, la durée, le résultat et le volume transféré. | V1.0 |
| TASK-005 | QUALITE | P0 | À faire | Demander une confirmation renforcée avant toute suppression de tâche si une action distante destructive est possible. | MVP |

## PLAN — Planification

**Périmètre :** Exécution manuelle, périodique ou planifiée des tâches et gestion des fenêtres horaires.

**État initial :** Cahier des charges

**Synthèse :** La planification doit couvrir les besoins simples sans imposer la compréhension de cron.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| PLAN-001 | FONC | P0 | À faire | Autoriser le lancement manuel d’une tâche. | MVP |
| PLAN-002 | FONC | P1 | À faire | Planifier une tâche toutes les N minutes/heures ou à une heure donnée. | V1.0 |
| PLAN-003 | FONC | P1 | À faire | Planifier les jours de la semaine et plusieurs plages horaires. | V1.0 |
| PLAN-004 | FONC | P2 | Demande | Ajouter un mode avancé compatible avec une expression cron, sans le rendre obligatoire. | Roadmap |
| PLAN-005 | FONC | P2 | Demande | Prévoir une fenêtre d’exclusion permettant de suspendre les transferts à certaines heures. | Roadmap |

## FILT — Filtres et exclusions

**Périmètre :** Règles d’inclusion/exclusion par chemin, nom, extension, taille, fichiers cachés et motifs personnalisés.

**État initial :** Cahier des charges

**Synthèse :** Les filtres doivent être compréhensibles, testables et visibles avant la première exécution réelle.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| FILT-001 | FONC | P1 | À faire | Exclure des dossiers, fichiers, extensions ou motifs de nom. | V1.0 |
| FILT-002 | FONC | P1 | À faire | Inclure uniquement certains dossiers, fichiers, extensions ou motifs. | V1.0 |
| FILT-003 | FONC | P2 | Demande | Filtrer selon une taille minimale et/ou maximale de fichier. | V1.0 |
| FILT-004 | FONC | P2 | Demande | Choisir le comportement pour fichiers cachés et éléments système. | V1.0 |
| FILT-005 | UI | P1 | À faire | Ajouter un outil de test des filtres indiquant quels fichiers seraient inclus ou exclus. | V1.0 |

## CONF — Conflits, suppressions et versions

**Périmètre :** Politique de suppression, collisions de fichiers, conservation, quarantaine et récupération après erreur utilisateur.

**État initial :** Cahier des charges

**Synthèse :** Aucune suppression irréversible ne doit être activée par défaut sans choix explicite de l’utilisateur.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| CONF-001 | FONC | P0 | À faire | Permettre de choisir si une suppression à la source est propagée à la destination. | MVP |
| CONF-002 | QUALITE | P0 | À faire | Utiliser par défaut une politique non destructive ou demander un choix explicite avant propagation des suppressions. | MVP |
| CONF-003 | FONC | P1 | À faire | En bidirectionnel, gérer les conflits avec au minimum : conserver le plus récent, conserver les deux ou nécessiter une intervention. | V1.0 |
| CONF-004 | FONC | P1 | À faire | Prévoir une quarantaine/corbeille optionnelle pour les fichiers remplacés ou supprimés côté local. | V1.0 |
| CONF-005 | FONC | P2 | Demande | Exploiter les fonctions de versioning du fournisseur Cloud lorsqu’elles existent, sans les supposer disponibles partout. | Roadmap |

## PERF — Transferts et performances

**Périmètre :** Débit, parallélisme, limites de bande passante, gros fichiers, reprise, files d’attente et impact sur le serveur Unraid.

**État initial :** Cahier des charges

**Synthèse :** Les transferts doivent être efficaces sans monopoliser le CPU, les disques ou la connexion Internet.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| PERF-001 | FONC | P1 | À faire | Configurer le nombre maximal de transferts simultanés. | V1.0 |
| PERF-002 | FONC | P1 | À faire | Configurer une limite de bande passante montante et descendante. | V1.0 |
| PERF-003 | FONC | P2 | Demande | Prévoir des limites de bande passante différentes selon les horaires. | Roadmap |
| PERF-004 | QUALITE | P1 | À faire | Tester le comportement avec de très nombreux petits fichiers et avec de gros fichiers. | V1.0 |
| PERF-005 | QUALITE | P1 | À faire | Prévoir reprise et nettoyage des transferts interrompus lorsque le moteur/protocole le permet. | V1.0 |

## SEC — Sécurité et authentification

**Périmètre :** Protection des secrets Cloud, OAuth/tokens, droits du conteneur, chiffrement, exposition réseau et journalisation sécurisée.

**État initial :** Cahier des charges

**Synthèse :** Les identifiants Cloud ne doivent jamais être exposés dans l’interface, les logs ou les fichiers exportés en clair.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| SEC-001 | QUALITE | P0 | À faire | Stocker les secrets/tokens de façon protégée et ne jamais les afficher en clair après enregistrement. | MVP |
| SEC-002 | QUALITE | P0 | À faire | Masquer systématiquement mots de passe, clés, tokens et secrets dans les logs et diagnostics. | MVP |
| SEC-003 | FONC | P1 | À faire | Privilégier OAuth ou jetons dédiés lorsqu’un fournisseur les supporte. | V1.0 |
| SEC-004 | QUALITE | P0 | À faire | Exécuter le conteneur avec les privilèges minimaux nécessaires et éviter le mode privileged. | MVP |
| SEC-005 | FONC | P2 | Demande | Proposer un chiffrement côté client des données distantes via un mécanisme compatible rclone crypt. | Roadmap |
| SEC-006 | QUALITE | P1 | À faire | Protéger l’interface Web lorsqu’elle est exposée au-delà du réseau local et documenter l’usage d’un reverse proxy HTTPS. | V1.0 |

## LOG — Historique, journaux et diagnostics

**Périmètre :** Historique des exécutions, fichiers traités, erreurs, débits, durées, logs techniques et diagnostic exportable.

**État initial :** Cahier des charges

**Synthèse :** Le diagnostic doit permettre de comprendre rapidement pourquoi une tâche a échoué, sans lire des logs Docker bruts.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| LOG-001 | FONC | P0 | À faire | Conserver l’historique des exécutions de chaque tâche avec état final, date, durée et volume transféré. | MVP |
| LOG-002 | FONC | P1 | À faire | Afficher la liste des fichiers transférés, ignorés, supprimés et en erreur pour une exécution. | V1.0 |
| LOG-003 | FONC | P1 | À faire | Prévoir plusieurs niveaux de logs : normal, détaillé et diagnostic. | V1.0 |
| LOG-004 | FONC | P2 | Demande | Exporter un package de diagnostic anonymisé contenant configuration non secrète, versions et logs utiles. | Roadmap |
| LOG-005 | QUALITE | P1 | À faire | Mettre en place une rotation/rétention configurable des logs pour éviter de remplir l’appdata. | V1.0 |

## NOTIF — Notifications

**Périmètre :** Alertes de succès, erreur, tâche bloquée, authentification expirée et autres événements importants.

**État initial :** Cahier des charges

**Synthèse :** Les notifications doivent être utiles mais configurables afin d’éviter le bruit.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| NOTIF-001 | FONC | P1 | À faire | Notifier les échecs de synchronisation et les tâches nécessitant une action. | V1.0 |
| NOTIF-002 | FONC | P1 | À faire | Intégrer lorsque possible le système de notifications Unraid. | V1.0 |
| NOTIF-003 | FONC | P2 | Demande | Permettre des notifications par webhook pour intégration à Home Assistant, Discord, Slack ou autres services. | Roadmap |
| NOTIF-004 | FONC | P2 | Demande | Permettre de choisir quels événements déclenchent une notification. | V1.0 |

## UI — Interface graphique et ergonomie

**Périmètre :** Tableau de bord, assistant de création de tâche, cartes de statut, tableaux, progression, navigation et cohérence visuelle.

**État initial :** Cahier des charges

**Synthèse :** L’interface doit reprendre la simplicité d’usage d’un outil NAS grand public tout en restant cohérente avec l’environnement Unraid.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| UI-001 | UI | P0 | À faire | Créer un tableau de bord affichant toutes les tâches et leur état : actif, pause, transfert, erreur ou terminé. | MVP |
| UI-002 | UI | P0 | À faire | Créer un assistant pas à pas de création/modification d’une tâche. | MVP |
| UI-003 | UI | P1 | À faire | Afficher en temps réel le fichier courant, la progression, le débit, le volume restant et le temps écoulé lorsqu’ils sont disponibles. | V1.0 |
| UI-004 | UI | P1 | À faire | Afficher des compteurs synthétiques : tâches actives, en pause, en erreur, prochain lancement et débit cumulé. | V1.0 |
| UI-005 | UI | P1 | À faire | Prévoir recherche, tri et filtres lorsque le nombre de tâches devient important. | V1.0 |
| UI-006 | UI | P2 | Demande | Prévoir thème clair/sombre ou adaptation automatique au thème Unraid si techniquement possible. | Roadmap |
| UI-007 | UI | P1 | À faire | Rendre l’interface responsive pour un usage correct sur écran de bureau, tablette et mobile. | V1.0 |

## UNRAID — Intégration Unraid et Community Applications

**Périmètre :** Packaging Docker, template Unraid, Community Applications, appdata, volumes, WebUI, icône, variables et documentation d’installation.

**État initial :** Cahier des charges

**Synthèse :** L’application doit pouvoir être installée et mise à jour comme une application Unraid standard.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| UNRAID-001 | FONC | P0 | À faire | Fournir une image Docker compatible avec l’usage standard d’Unraid. | MVP |
| UNRAID-002 | FONC | P0 | À faire | Fournir un template XML Unraid propre avec WebUI, icône, port, appdata et mappings de stockage. | MVP |
| UNRAID-003 | FONC | P0 | À faire | Persister configuration, base locale, historiques et secrets dans un dossier appdata dédié. | MVP |
| UNRAID-004 | QUALITE | P1 | À faire | Documenter clairement les mappings de volumes nécessaires pour donner accès aux shares à synchroniser. | V1.0 |
| UNRAID-005 | FONC | P1 | À faire | Préparer les métadonnées nécessaires à une publication dans Unraid Community Applications. | V1.0 |
| UNRAID-006 | QUESTION | P1 | À faire | Choisir le nom définitif de l’application après vérification qu’il est explicite et non déjà utilisé dans Community Applications. | Avant V1.0 |

## DATA — Configuration, sauvegarde et restauration

**Périmètre :** Base de configuration, export/import, sauvegarde, restauration et migration des paramètres entre versions ou serveurs.

**État initial :** Cahier des charges

**Synthèse :** La configuration doit être portable et récupérable sans devoir recréer manuellement toutes les tâches.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| DATA-001 | FONC | P0 | À faire | Conserver les tâches, paramètres et historiques dans un stockage persistant sous appdata. | MVP |
| DATA-002 | FONC | P1 | À faire | Exporter la configuration de l’application dans un fichier de sauvegarde. | V1.0 |
| DATA-003 | FONC | P1 | À faire | Restaurer/importer une configuration exportée avec contrôle de compatibilité. | V1.0 |
| DATA-004 | QUALITE | P0 | À faire | Prévoir des migrations de données/configuration lors des changements de version sans perdre les tâches existantes. | Permanent |
| DATA-005 | QUALITE | P1 | À faire | Ne jamais inclure les secrets en clair dans un export de diagnostic ou une sauvegarde non protégée. | V1.0 |

## UPDATE — Versions et mises à jour

**Périmètre :** Versionnement, image Docker, migrations, notes de version, canal stable/bêta et compatibilité des configurations.

**État initial :** Cahier des charges

**Synthèse :** Une mise à jour doit être réversible autant que possible et ne pas altérer les tâches existantes.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| UPDATE-001 | QUALITE | P0 | À faire | Versionner l’application et l’image Docker de manière explicite. | MVP |
| UPDATE-002 | QUALITE | P0 | À faire | Tester les migrations de configuration avant publication d’une mise à jour. | Permanent |
| UPDATE-003 | FONC | P2 | Demande | Prévoir un canal Stable et éventuellement un canal Beta pour les utilisateurs volontaires. | Roadmap |
| UPDATE-004 | FONC | P2 | Demande | Afficher les notes de version et changements importants depuis l’interface. | Roadmap |

## AIDE — Aide et documentation

**Périmètre :** Documentation d’installation, création de compte Cloud, tâches, filtres, sécurité, résolution d’erreurs et FAQ.

**État initial :** Cahier des charges

**Synthèse :** L’application doit rester utilisable par un administrateur Unraid non spécialiste de rclone.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| AIDE-001 | FONC | P1 | À faire | Ajouter une aide intégrée pour créer un stockage Cloud et une tâche de synchronisation. | V1.0 |
| AIDE-002 | FONC | P1 | À faire | Documenter les différences entre upload, download et bidirectionnel ainsi que le comportement des suppressions. | V1.0 |
| AIDE-003 | FONC | P2 | Demande | Créer une FAQ de diagnostic pour authentification expirée, droits locaux, quota Cloud et erreurs réseau. | Roadmap |
| AIDE-004 | FONC | P2 | Demande | Documenter une procédure sûre de sauvegarde/restauration de l’appdata avant changement majeur. | V1.0 |

## GLOBAL — Architecture, qualité et éléments transverses

**Périmètre :** Architecture générale, moteur rclone, API interne, interface Web, persistance, tests, sécurité, performances et prévention des régressions.

**État initial :** Cahier des charges

**Synthèse :** Cible proposée : application Web conteneurisée pour Unraid, utilisant rclone comme moteur de transfert, avec couche applicative dédiée à la configuration, la planification, la supervision et l’UX.

| ID | Type | Priorité | Statut | Sujet | Version |
|---|---|---|---|---|---|
| GLOBAL-001 | QUESTION | P0 | À faire | Valider rclone comme moteur principal afin de ne pas réimplémenter les protocoles et fournisseurs Cloud. | Avant MVP |
| GLOBAL-002 | QUALITE | P0 | À faire | Séparer l’interface Web, la logique de tâches et le moteur de transfert afin de faciliter maintenance et tests. | MVP |
| GLOBAL-003 | QUALITE | P0 | À faire | Prévenir les régressions par tests automatisés sur création de tâche, simulation, transfert, suppression et restauration de configuration. | Permanent |
| GLOBAL-004 | QUALITE | P0 | À faire | Ne jamais considérer une tâche destructive comme validée sans test dry-run puis test sur jeu de données dédié. | Permanent |
| GLOBAL-005 | QUALITE | P1 | À faire | Supporter correctement les noms Unicode, espaces, caractères spéciaux et arborescences profondes. | V1.0 |
| GLOBAL-006 | QUALITE | P1 | À faire | Prévoir des tests sur perte réseau, redémarrage du conteneur, quota distant atteint et expiration d’authentification. | V1.0 |
| GLOBAL-007 | QUALITE | P1 | À faire | Conserver des identifiants stables pour toutes les tâches et exécutions afin de faciliter le suivi et le support. | MVP |

---

# 27. Règles de livraison Claude Code

Avant de considérer une tâche terminée :

1. afficher les fichiers modifiés ;
2. résumer le changement en quelques lignes ;
3. indiquer les tests réellement exécutés ;
4. signaler clairement les tests non exécutables ;
5. ne pas marquer `Disponible` une fonction seulement compilée mais non testée ;
6. conserver la compatibilité des données existantes ;
7. pour toute migration SQLite, écrire un test de migration depuis la version précédente ;
8. ne jamais modifier une règle destructive sans ajouter ou adapter les tests associés ;
9. ne jamais désactiver un test pour faire passer la build sans explication et décision explicite ;
10. ne jamais masquer une erreur rclone importante derrière un simple message « Échec » : conserver la cause technique et afficher une version compréhensible à l'utilisateur.

## Format conseillé de compte rendu

```text
ID : SYNC-004
Statut : À tester
Version : 0.1.0

Fait :
- ...

Tests :
- ...

Risques / limites :
- ...

Fichiers modifiés :
- ...
```

---

# 28. Interdictions d'architecture

Ne pas introduire sans décision explicite :

- Docker Compose comme prérequis d'installation Unraid ;
- base PostgreSQL/MySQL pour le MVP ;
- Redis ;
- Celery ;
- Kubernetes ;
- accès au Docker socket ;
- mode privileged ;
- shell construit par concaténation de paramètres utilisateur ;
- suppression distante activée par défaut ;
- lancement automatique d'un resync bidirectionnel ;
- télémétrie externe implicite ;
- réécriture d'un connecteur Cloud déjà correctement fourni par rclone.

---

# 29. Source de vérité et cohérence

Ce fichier doit rester cohérent avec le JSON de suivi de projet. En cas de divergence :

1. ne pas choisir silencieusement une version ;
2. conserver l'exigence la plus sûre ;
3. signaler la divergence ;
4. mettre à jour les deux référentiels lors de la décision.

**Règle finale : aucune optimisation de confort, de vitesse ou de simplicité ne justifie de réduire les protections contre la perte de données.**
