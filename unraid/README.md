# Dépôt de templates Unraid

Ce dossier contient tout ce qu'attend le portail de soumission de
**Community Applications**. Il a vocation à être publié dans un dépôt GitHub
**distinct** de celui de l'application — c'est ce dépôt-là que le robot de CA
scanne.

```
unraid/
├── ca_profile.xml              profil du dépôt (auteur, support)
├── cloud-sync-manager.xml      template de l'application
├── images/
│   ├── cloud-sync-manager.png      512×512, à référencer dans <Icon>
│   └── cloud-sync-manager-128.png  déclinaison de contrôle
└── LICENSE                     MIT — exigence OSI de CA
```

## Avant de soumettre

| Valeur | État | Détail |
|---|---|---|
| Compte GitHub | ✅ | `renaldsouder`, en dur dans les deux XML |
| Dépôt de templates | ✅ | `renaldsouder/unraid-templates` |
| Image publiée | ✅ | `ghcr.io/renaldsouder/cloud-sync-manager`, poussée par la CI |
| Fil de support | ⏳ | `Support` pointe provisoirement sur les *issues* GitHub |

La CI (`.github/workflows/ci.yml`) n'a **pas** besoin du pseudonyme : GitHub
le fournit à l'exécution via `github.repository_owner`. Seuls les deux XML,
statiques et lus par Community Applications, le portent en dur.

**Le fil de support reste à ouvrir.** `Support` désignait auparavant une URL
de forum inexistante, qui renvoyait 404 : mieux vaut une page d'issues qui
répond qu'un lien mort. Community Applications attend cependant un fil sur
les forums Unraid — il faudra le créer et remplacer l'URL dans les deux XML
**avant** toute soumission, le portail vérifiant que l'adresse répond.

## Ce dépôt n'est pas une soumission

Publier ces fichiers rend l'installation par URL de template possible et
permet de l'éprouver. Cela ne met l'application dans aucun catalogue : rien
n'apparaît dans Community Applications tant que la soumission n'a pas été
faite, et elle ne doit pas l'être avant que le §22 soit tenu — 30 jours
d'utilisation réelle sans perte silencieuse, et plusieurs fournisseurs
réellement éprouvés (§20.4).

## Vérifier le template

```bash
python -c "import xml.etree.ElementTree as E; E.parse('unraid/cloud-sync-manager.xml'); print('XML valide')"
```

Les onze balises exigées par le portail sont présentes : `Name`, `Repository`,
`Registry`, `Network`, `Shell`, `Privileged`, `Support`, `Project`,
`Overview`, `Category`, `TemplateURL`.

## Compatibilité des installations existantes

Le §17 du cahier des charges le rappelle : les réglages d'une application
Unraid sont figés dans le XML côté serveur de l'utilisateur. Après publication,
**ne jamais renommer une variable d'environnement ni une cible de volume** —
seulement en ajouter. Un renommage réinitialiserait silencieusement le réglage
chez tous ceux qui ont déjà installé l'application.
