"""Notes de version affichées dans l'interface (UPDATE-004).

Écrites pour l'utilisateur d'Unraid, pas pour le développeur : ce qui
change pour lui, et surtout ce qui change dans la manière dont ses données
sont protégées. Les identifiants de backlog restent dans l'historique git.

``important`` est réservé à ce qui demande une action ou modifie un
comportement destructif : ce sont ces lignes que l'interface met en avant.

Le bloc dont ``version`` vaut ``None`` décrit ce qui est fusionné mais pas
encore publié. À la publication, on lui donne son numéro et sa date — un
test vérifie que la version en cours a toujours ses notes.
"""

from __future__ import annotations

from typing import Any

RELEASES: list[dict[str, Any]] = [
    {
        "version": None,
        "date": None,
        "important": [
            "Après une erreur temporaire (coupure réseau, fournisseur "
            "indisponible), une tâche repart d'elle-même après 5, 15 puis "
            "45 minutes, en repassant par toutes les vérifications. Une "
            "simulation, une suppression confirmée et le bidirectionnel ne "
            "sont jamais relancés d'eux-mêmes. Réglable ou désactivable avec "
            "CSM_RETRY_DELAYS_MINUTES.",
        ],
        "highlights": [
            "Comparaison par somme de contrôle, pour détecter un fichier "
            "modifié sans changement de taille ni de date. Appliquée seulement "
            "si les deux côtés partagent un algorithme.",
            "Réglages de transfert dans l'éditeur de tâche : transferts "
            "simultanés, limite de bande passante, y compris selon l'heure.",
            "Planification : plage horaire exclue, et expression cron en mode "
            "avancé.",
            "Paramètres → Diagnostic : un fichier anonymisé à joindre à une "
            "demande d'aide, sans aucun identifiant.",
            "Aide : une section Dépannage, par symptôme.",
            "Le test d'un stockage affiche l'espace utilisé et libre.",
            "Interface utilisable sur téléphone et tablette.",
        ],
    },
    {
        "version": "0.3.0",
        "date": "2026-10-14",
        "important": [
            "Le mode Bidirectionnel est disponible. Il demande une "
            "initialisation explicite, simulée d'abord, et n'a encore été "
            "éprouvé contre aucun fournisseur réel : commencez sur un dossier "
            "de test.",
        ],
        "highlights": [
            "Assistant de première utilisation.",
            "Explorateurs de dossiers, local et distant, au lieu de taper les "
            "chemins.",
            "Modifier, dupliquer une tâche ; confirmation renforcée avant de "
            "supprimer une tâche destructive.",
            "Trois niveaux de journalisation : normal, détaillé, diagnostic.",
            "Recherche et tri des tâches.",
            "Aide intégrée sur les modes, les sens et les suppressions.",
        ],
    },
    {
        "version": "0.2.4",
        "date": "2026-09-10",
        "important": [],
        "highlights": [
            "Détail fichier par fichier de chaque exécution, avec recherche "
            "par chemin. Une liste tronquée le signale.",
        ],
    },
    {
        "version": "0.2.3",
        "date": "2026-09-10",
        "important": [],
        "highlights": [
            "OneDrive : identification du disque plus robuste, et réparable "
            "en un clic.",
        ],
    },
    {
        "version": "0.2.2",
        "date": "2026-09-10",
        "important": [],
        "highlights": [
            "L'autorisation OAuth passe par le port de l'interface ; le port "
            "53682 n'est plus nécessaire.",
        ],
    },
    {
        "version": "0.2.1",
        "date": "2026-09-09",
        "important": [],
        "highlights": [
            "Une interface sans mot de passe est signalée sur chaque écran.",
        ],
    },
    {
        "version": "0.2.0",
        "date": "2026-09-09",
        "important": [],
        "highlights": [
            "Autorisation OAuth (Google Drive, OneDrive, Dropbox) conduite "
            "depuis l'interface, sans terminal.",
        ],
    },
    {
        "version": "0.1.2",
        "date": "2026-09-09",
        "important": [],
        "highlights": [
            "Options avancées des fournisseurs accessibles — indispensables "
            "à OneDrive.",
        ],
    },
    {
        "version": "0.1.1",
        "date": "2026-09-09",
        "important": [
            "L'interface peut être protégée par un mot de passe : Paramètres "
            "→ Accès à l'interface.",
        ],
        "highlights": [],
    },
    {
        "version": "0.1.0",
        "date": "2026-09-09",
        "important": [],
        "highlights": [
            "Première version : Copie et Miroir dans les deux sens, simulation "
            "obligatoire, seuils de suppression, corbeille, planification, "
            "filtres, notifications Unraid et webhook, sauvegarde de la "
            "configuration.",
        ],
    },
]
