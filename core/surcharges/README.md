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

Tests (#259) : `tests/test_surcharge_agent.py`, livrés par le lot 15 du chantier
#243 (#267). Trois étages, dans l'ordre où une surcharge traverse le produit — le
**dépôt** (la surcharge vide qui ne se stocke pas, `herite()`, le refus d'un nom
hors `NOMS_DU_CODE`), le **catalogue effectif** (le seul chemin par lequel elle
atteint l'exécution : `MAESTRO_MODEL` prime sur le modèle et pas sur l'effort, le
fournisseur reste déclaratif), et les **deux routes** (les trois états de
`source`, `herite`/`reglages_du_code`, et surtout *annuler n'est pas supprimer*).

## Rangement par projet (#1038)

Depuis [docs/37 §2.1](../../docs/37-decision-equipe-sur-mesure.md), **un agent
appartient à un projet**. Ce dossier porte donc deux niveaux :

- **la racine** — les **gabarits de rôle**, ce qui vaut hors de tout projet et ce
  que l'analyse d'équipe consultera (#1039) ;
- **`_projets/<projet_id>/`** — la même arborescence, pour un projet. Le tiret bas
  le met hors d'atteinte d'un nom d'agent, qui commence par `[a-z0-9]`.

L'API sert ces deux niveaux par `?projet=<id>` (omis : les gabarits), et
l'exécution lit ceux du projet de la tâche. Code :
`maestro/agents/rangement.py` (la règle), `maestro/agents/configuration.py`
(les six dépôts d'un bloc).

**Ce que le projet ne règle pas, il l'hérite du gabarit.** Une surcharge que le projet n'a pas
posée est celle du gabarit. Annuler une surcharge dans un projet le ramène donc au
gabarit — c'est-à-dire à l'agent du code tant que rien n'y est surchargé, l'invariant
du #259 dans le nouveau rangement.

**Reprise.** Ce qu'un poste portait ici avant #1038 est rattaché au projet qui
l'utilise au démarrage de l'API — idempotente, sans rien supprimer, et elle dit
ce qu'elle a fait. À la main : `python -m maestro.agents.reprise [--check]
[--projet <id>]` (`MAESTRO_REPRISE_AGENTS=0` pour s'en passer).
