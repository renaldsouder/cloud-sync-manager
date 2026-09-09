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

Quatre valeurs restent à renseigner. Elles sont marquées `VOTRE-COMPTE` ou
`XXXXXX` dans les deux fichiers XML.

| Valeur | Où | Comment l'obtenir |
|---|---|---|
| Compte GitHub | `Repository`, `Registry`, `Project`, `TemplateURL`, `Icon`, `ca_profile.xml` | Votre pseudonyme GitHub |
| Nom du dépôt de templates | `TemplateURL`, `Icon`, `ca_profile.xml` | Par convention `unraid-templates` |
| Fil de support | `Support`, `ca_profile.xml` | À créer sur les forums Unraid, **après** avoir une image publiable |
| Image publiée | `Repository` | Poussée sur GHCR par la CI, en `ghcr.io/<compte>/cloud-sync-manager` |

La CI (`.github/workflows/ci.yml`) n'a **pas** besoin du pseudonyme : GitHub
le fournit à l'exécution via `github.repository_owner`. Seuls les deux XML,
statiques et lus par Community Applications, le portent en dur.

L'ordre importe : l'image doit exister et être installable avant d'ouvrir le
fil de support, et le fil doit exister avant la soumission — le portail
vérifie que l'URL répond.

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
