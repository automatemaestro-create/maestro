<!-- documentation: produit -->
# 57 — Un courtier de conteneur pour l'agent confiné

**Décision du 2026-11-18** (#1723), rendue par la personne en ces mots : « V2, vérification comprise ».
Chantier : **#1724**.

## 0. Ce que cette note arrête

- **Un agent confiné à l'intégrité basse obtient le verdict au conteneur par un courtier que Maestro
  tient.** L'agent demande une commande dans un conteneur. Maestro, au niveau de la personne, compose
  lui-même ce conteneur sur l'espace de la session, sur le contrat de
  [docs/17 §3](./17-isolation-execution.md), le joue, puis rend la sortie et le code de retour.
- **Le code que l'agent écrit ne joue jamais sur le poste au-dessus de l'intégrité basse.** Il joue
  dans un conteneur, frontière que le mode isolé tient déjà.
- **Le tube du moteur reste fermé à l'agent.** Qui le commande monte n'importe quel dossier que la
  personne peut écrire, et y écrit ([docs/17 §7](./17-isolation-execution.md), #1714).
- **La vérification d'une tâche passe sous la borne du poste**, comme celle de l'outillage (#1691).
  Ce qui demande un conteneur y passe par le même courtier. Elle cesse donc de jouer au niveau de la
  personne le code que l'agent a écrit, sans perdre le régime conteneur.

## 1. La question

Le moteur de Docker Desktop refuse un processus à l'intégrité basse, qu'il tourne ou non (mesuré
le 2026-11-17, #1714). Depuis #1515, l'agent naît à ce niveau, et le filet qu'il lance aussi. Ce
filet joue donc en natif, et le dit ([docs/10 §8.4](./10-workflow-git.md)).
- **Le verdict ne change pas.**
- **La durée change** : 52 suites en 35 min 26 s sur #1688. La suite entière tient en 1 min 51 au
  conteneur (mesure du 2026-08-21, en-tête de `scripts/ci/pytest.Dockerfile`).
- **L'OS du verdict change** : Windows, et non plus Linux, celui de la CI (#372).

La question posée : par quelle voie le verdict au conteneur revient-il à l'agent sans lui ouvrir le
tube, et qu'ouvre chaque voie au regard de [docs/19](./19-securite-modele-de-menace.md) ?

## 2. Ce qui a été vérifié avant de choisir

- **L'agent** : son CLI et tout ce qu'il lance naissent abaissés (`maestro/sandbox/low_integrity.py`).
- **La vérification de l'outillage** joue sous la borne des agents (`borne_du_poste`, #1691). Elle
  est donc abaissée, et ne voit pas Docker non plus.
- **La vérification d'une tâche joue au niveau de la personne.** `maestro/engine/verification.py`
  appelle `execution.jouer` sans abaissement. Sur #1688, elle a joué `bash scripts/ci/local.sh` dans
  l'espace de la livraison.
  - Le texte de ses commandes vient du vérificateur. Le code qu'elles exécutent vient de l'agent :
    ses tests, son `conftest.py`, ses scripts.
  - C'est le vecteur de #1502 : un script que la portée ne lit pas, joué ici au-dessus du
    confinement.

## 3. Les voies, et ce que chacune ouvre

| Voie | Ce qu'elle ouvre | Sort |
|---|---|---|
| **V1** — Maestro joue la commande déclarée au niveau de la personne | La borne ne porte que sur le texte de la commande. Celle-ci exécute les scripts et les tests de l'espace, et une ligne dans un test joue `taskkill //IM python.exe //F` au niveau de la personne. La déclaration elle-même vit dans `AGENTS.md` ([docs/38 §3.5bis](./38-decision-outillage-universel-du-projet.md)), dans l'arbre de l'agent. Au total, l'équivalent de `MAESTRO_ISOLATION=hote` pour tout code que l'agent fait entrer dans son filet | écartée |
| **V2** — Un courtier de conteneur, verbe de Maestro | §4 | **retenue** |
| **V3** — Un mandataire filtrant de l'API Docker | Il serait transparent pour un filet qui appelle `docker`. Mais il faudrait valider champ par champ le contrat d'un tiers : `Binds`, `Mounts`, un volume `local` monté par `o=bind`, `Privileged`, `PidMode`, `Devices`, un `exec` dans un autre conteneur, l'installation d'un greffon… Chaque version du moteur peut en ajouter un, et un champ oublié rouvre le tube. C'est l'inverse de [docs/44](./44-decision-maestro-possede-ses-contrats.md) | écartée pour l'instant |
| **V4** — Un moteur dédié à l'agent (distribution WSL, machine Podman) | Toutes les distributions WSL 2 partagent un même noyau, et d'autres distributions peuvent modifier des réglages du noyau qui touchent la sécurité de Docker Desktop (documentation de Docker). Monter l'espace demande un partage de fichiers, que la VM sert avec les droits de la personne, et qui reste à mesurer. Il faut aussi une installation, avec des droits d'administrateur la première fois | écartée pour l'instant |
| **V5** — L'agent en mode isolé (`MAESTRO_ISOLATION=conteneur`) | Elle n'ouvre rien. Mais c'est un réglage du poste : il change tout le régime de l'agent pour une question de filet. Il apporte aussi une image sans la chaîne du projet, des plafonds de 2 CPU, 2 Go et 256 pids, et aucun navigateur | reste une option du poste |
| **V0** — Le statu quo de #1714 | Le verdict est juste. Le régime et la durée ne le sont pas | remplacé par V2 |

## 4. Le courtier

C'est le schéma du bac à sable de Chromium. La cible abaissée demande, et le courtier confronte la
demande à sa politique. Il fait ensuite lui-même l'opération permise, et rend le résultat.

**Ce que l'agent nomme.** L'agent demande une commande dans un conteneur, par un programme de
Maestro joignable depuis l'intégrité basse. Un script qu'il lance peut faire la même demande. Il
nomme l'image (une image, ou un Dockerfile de son espace) et la commande. Il ne nomme ni montage, ni
réseau, ni privilège, ni dossier.

**Ce que Maestro compose.** Maestro compose le conteneur au niveau de la personne, sur le contrat de
[docs/17 §3](./17-isolation-execution.md) et par son code (`maestro/sandbox/container.py`,
`maestro/sandbox/depot_conteneur.py`), sans jamais un second contrat :
- seul l'espace de la session est monté, avec ses masques ;
- un projet versionné reçoit le dépôt du conteneur ;
- la racine est en lecture seule, l'utilisateur n'est pas root, avec `--cap-drop ALL`,
  `no-new-privileges` et les plafonds ;
- le réseau est sortant seulement.

**Ce qu'il ouvre :**
1. **Un verbe.** L'agent fait naître des conteneurs sur le moteur de la personne, à sa demande.
   - Un jeton de session lie le verbe à l'espace de cette session.
   - Ce jeton ne voyage pas sous `MAESTRO_*`, que le lanceur retient au CLI : il se nomme à part
     ([docs/17 §8](./17-isolation-execution.md)).
   - Un autre agent abaissé peut lire ce jeton, puisque deux agents bas se voient
     ([docs/17 §7](./17-isolation-execution.md)). Il n'obtient alors que l'espace de l'autre, qu'il
     peut déjà écrire.
2. **La construction d'une image depuis un Dockerfile que l'agent écrit.**
   - BuildKit joue ses étapes dans ses propres conteneurs.
   - Maestro ne passe aucun droit étendu (`network.host`, `security.insecure`), aucun secret et aucun
     agent ssh.
   - Le contexte est l'espace masqué, jamais la racine.
   - Une directive `# syntax=` tire un frontend tiers, qui joue dans BuildKit sans plus de droits.
   - Le cache de construction du moteur est celui de la personne (mesuré par #1726). Les caches
     montés sont rangés sous l'espace de noms du projet, qu'un frontend tiers ne tient pas
     ([docs/17 §7](./17-isolation-execution.md)).
3. **Le disque du moteur.** `docker_data.vhdx` ne rétrécit jamais
   ([docs/10 §8.4](./10-workflow-git.md)). Le courtier garde donc :
   - une étiquette par projet ;
   - le retrait de la précédente au moment de la construction (le parti pris de #463) ;
   - un conteneur à la fois par projet, sous ses plafonds.
4. **La frontière du conteneur.** Une évasion par le noyau ou le runtime atterrit dans la VM de
   Docker Desktop, qui voit les disques partagés de la personne. C'est l'hypothèse que le mode isolé
   fait déjà ([docs/17 §1](./17-isolation-execution.md)), et Docker la dit plus faible sous WSL 2 que
   sous Hyper-V.
5. **Le réseau.** Il sort, comme celui de l'agent. `host.docker.internal` joint le bouclage de
   l'hôte ([docs/17 §5](./17-isolation-execution.md)), que l'agent joint déjà.

**Ce qu'il ferme mieux que l'intégrité basse.** Le conteneur ne lit pas le profil de la personne.
Il ne voit pas non plus les dossiers étiquetés bas des autres agents.

**Ce qu'il ne couvre pas.**
- **Un filet qui commande Docker lui-même** (testcontainers, compose). Un tel filet ne passe par le
  courtier que s'il sait le demander.
- **Linux et macOS.** Rien n'y est abaissé, et le moteur n'y refuse donc aucun niveau.

**Docker n'est qu'un adaptateur.** Le verbe est celui de Maestro, et Podman ou un runtime Linux le
servirait sans changer de contrat ([docs/44](./44-decision-maestro-possede-ses-contrats.md)). Le
régime se choisit sur un fait (le courtier est là), jamais sur le texte d'une erreur.

## 5. La vérification d'une tâche

Elle passe sous `borne_du_poste`, comme celle de l'outillage. Une commande qui écrit hors de
l'espace et de son établi y est refusée par le poste. Ce qui demande un conteneur y passe par le
courtier, sur l'espace de la livraison.

[docs/17 §7](./17-isolation-execution.md) et [docs/19 §2.4](./19-securite-modele-de-menace.md)
l'ont retirée de « ce que l'isolation ne couvre pas » avec le lot qui la livre (#1728), et disent
comment elle joue.

## 6. Ce qui ne bouge pas

- L'intégrité basse reste le défaut sous Windows, et le mode isolé une option du poste.
- La portée reste l'arbitre : le poste borne ce que le texte ne dit pas, il ne remplace pas le
  jugement.
- Le tube du moteur n'est ouvert à aucun processus abaissé.

## 7. Les suites

<!-- documentation: développement -->

Le chantier **#1724** porte quatre lots en série : `lot::arbitre`, aucun lot n'est parallélisable.

1. **#1725, le socle.** Un agent abaissé joue une commande dans un conteneur que Maestro compose, sur
   une image nommée. Le lot comprend le canal, le jeton, les refus motivés et « La machine ».
2. **#1726.** L'image se construit depuis un Dockerfile de l'espace, et la précédente est ramassée.
3. **#1727.** Le filet du dépôt de Maestro choisit le courtier à l'intégrité basse. Preuve sur le
   réel : le périmètre de #1688.
4. **#1728.** La vérification d'une tâche passe sous la borne du poste et par le courtier.

**Sa place dans la file.** #1725, #1726 et #1728 touchent `maestro/sandbox/` et `maestro/engine/`,
que les lots de traduction #1656 (`sandbox`) et `engine` renomment
([docs/55](./55-decision-le-code-s-ecrit-en-anglais.md)). Un lot du courtier et un lot de
traduction du même paquet ne sont pas en vol en même temps. Le code neuf du courtier s'écrit en
anglais, et son nom entre d'abord au glossaire de [docs/08](./08-glossaire.md).

## Sources vérifiées

- Docker, *Enhanced Container Isolation — limitations* : « All WSL 2 distributions share the same
  Linux kernel instance ». La même page décrit une isolation réduite sous WSL 2 par rapport à
  Hyper-V. <https://docs.docker.com/enterprise/security/hardened-desktop/enhanced-container-isolation/limitations/>
- Chromium, *Sandbox design* : le courtier exécute pour la cible les appels que sa politique
  permet. <https://chromium.googlesource.com/chromium/src/+/main/docs/design/sandbox.md>
