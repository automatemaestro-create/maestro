# core/surcharges — Réglages de modèle posés sur les agents du code

Dépôt des **surcharges** des agents définis par le code (ticket #259) : depuis
la Control Tower, on change le **fournisseur**, le **modèle** ou l'**effort**
d'un agent du catalogue par défaut *sans le dupliquer* en agent personnalisé.

## Pourquoi ce dépôt existe

Le catalogue avait deux états — « du code » (`maestro/agents/catalog.py`) et
« personnalisé » (`core/agents/`, #72). Changer le modèle d'un agent du code
imposait donc de le **cloner** : recopier son playbook et ses compétences pour
ne toucher qu'un réglage, après quoi les deux exemplaires divergent en silence
et l'agent cesse de suivre les améliorations du code.

D'où le **troisième état**, « du code, surchargé » : l'agent reste celui du
code — rôle, compétences et playbook continuent d'en venir et d'en suivre les
évolutions —, et seuls les réglages posés ici le recouvrent.

## Fonctionnement

- Un fichier par agent surchargé : `<nom>.json` (`fournisseur`, `modele`,
  `effort`, horodaté). Un agent **sans fichier** est celui du code, tel quel.
- Un réglage **absent** du fichier n'est pas un réglage vide : c'est un réglage
  **hérité**, que l'API nomme dans `herite` pour que l'écran le marque comme tel
  plutôt que de le faire deviner.
- Une surcharge **vide ne se stocke pas** : la poser sans aucun réglage revient
  à l'annuler, et le fichier est retiré. Sans cette règle, « surchargé avec
  rien » existerait à côté de « du code », deux états indiscernables à l'usage
  dont l'un afficherait pourtant l'agent comme modifié.
- Effet à l'exécution : `modele` et `effort` atteignent le moteur par
  `maestro.agents.store.catalogue()`, le seul endroit où le catalogue effectif
  s'assemble — moteur, workers et activités durables en héritent sans une ligne.
  `fournisseur` reste **déclaratif au POC**, comme sur une définition
  personnalisée : le moteur exécute sur `MAESTRO_PROVIDER`.
- `MAESTRO_MODEL` (#69) prime sur une surcharge de modèle, comme il prime sur le
  modèle d'un agent personnalisé : c'est une bascule globale.
- Lecture/écriture par le code : `maestro.agents.store.SurchargeStore` ; par
  HTTP : `PUT`/`DELETE /api/catalogue/{nom}/reglages` ; depuis l'UI : l'onglet
  **Profil** d'un agent du code.
- Racine remplaçable par `MAESTRO_SURCHARGES_DIR` (cf. `.env.example`).

## Surcharger n'est pas supprimer

Une surcharge **s'annule** (retour aux réglages du code, l'agent reste au
catalogue) ; un agent personnalisé **se supprime** (il disparaît). Les deux
gestes ne doivent pas se confondre : `DELETE /api/catalogue/{nom}` reste refusé
en 403 sur un agent du code, et `DELETE /api/catalogue/{nom}/reglages` refusé en
403 sur un agent personnalisé — dont la définition *est* son réglage, et se
modifie directement par `PUT /api/catalogue/{nom}`. Deux chemins d'écriture vers
la même valeur sont exactement ce que #259 supprime côté playbook.

Les surcharges écrites ici sont des **données d'exécution** : elles ne sont pas
commitées (voir `.gitignore`). Moteur, workers et API Control Tower doivent voir
le même stockage au POC (fichiers partagés). En V1, ce stockage passera en base
(champs de l'entité `AGENT`, docs/03) sans changer le contrat.

Tests (#259) : différés au lot 15 du chantier #243.
