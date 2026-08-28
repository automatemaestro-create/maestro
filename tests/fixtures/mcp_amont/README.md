# Corpus capturé du registre MCP officiel (#680, lot 6/6 du parent #673)

`corpus.jsonl` porte **62 enveloppes de listing** du registre MCP officiel,
**verbatim** — une par ligne, exactement la forme `{"server": …, "_meta": …}` que
sert `GET /v0.1/servers`. Aucun champ n'a été réécrit, réordonné ni élagué : ce
que le corpus perd, il le perd en cessant d'être un échantillon.

## Pourquoi capturer plutôt qu'interroger

**Aucun test ne parle au registre en direct.** Le registre est en préversion et
annonce lui-même « no uptime or data durability guarantees » : une suite qui en
dépendrait rendrait un rouge qui n'apprend rien — ni sur notre code, ni sur le
sien —, et elle le rendrait le jour où l'on a le moins envie d'enquêter. La
capture est donc faite **une fois**, versionnée, et relue hors ligne.

**Pourquoi ne pas se contenter de documents écrits à la main.** La majorité de
[`tests/test_mcp_traduction.py`](../../test_mcp_traduction.py) est fabriquée, et
c'est la bonne forme pour éprouver une règle à la fois. Mais un document
fabriqué ne surprend jamais son auteur : il ne répond pas à « est-ce que ça tient
sur ce que l'amont sert vraiment ? ». Les deux moitiés se complètent, aucune ne
remplace l'autre.

## Ce que la capture a mesuré — 2026-08-28

4 029 entrées parcourues (40 pages de listing, plus 10 pages d'une fenêtre
incrémentale `include_deleted=true`, qui est le seul endroit où vivent les
`deleted` et les `deprecated`), 62 retenues par **couverture de formes** — au
plus 4 par catégorie. La sélection ne vise donc **pas** la représentativité
statistique : elle vise que chaque branche du lecteur ait de la matière réelle.

Ce que le corpus donne quand on le passe à `traduire_entree` :

| Fait | Valeur |
|---|---|
| Traduites | **41 / 62** |
| Refusées | 21 — `registre_non_supporte` 8, `entree_supprimee` 4, `sans_forme` 4, **`variable_en_argv` 3**, `variable_en_url` 2 |
| Transports rendus | `http` 26, `stdio` 13, `sse` 2 |
| Modes d'auth dérivés | `sans_secret` 28, `token_statique` 13 |
| Statuts amont | `active` 54, `deleted` 4, `deprecated` 4 |
| **Millésimes de `$schema`** | **cinq** : `2025-12-11` (43), `2025-10-17` (6), `2025-09-29` (5), `2025-07-09` (4), `2025-09-16` (4) |

⚠ **Le parent (#673) annonçait deux millésimes en circulation ; il y en a cinq.**
`SCHEMAS_CONNUS` en déclare deux, donc **14 entrées** du corpus portent
l'avertissement « schéma amont inconnu » — et se traduisent quand même. C'est
exactement le comportement que le module promet (un millésime hors table est
**signalé**, jamais refusé), et c'est le genre de fait qu'un document fabriqué
n'aurait pas rendu. Élargir `SCHEMAS_CONNUS` ferait taire l'avertissement ; ce
n'est pas la même chose que le rendre inutile.

⚠ **Les trois échantillons fautifs en argv sont réels** — `ai.codenib/codenib`,
`aws.api.us-east-1.ecs-mcp/server`, `aws.api.us-east-1.eks-mcp/server`. Le refus
`variable_en_argv` est donc **prouvé sur de la matière capturée** avant que le
balayage ne conclue de son absence parmi les entrées traduites : c'est le seul
ordre qui distingue un garde-fou d'un ✓ sur une question jamais posée.

## Refaire la capture

```
node tests/fixtures/mcp_amont/capturer.mjs [pages]
```

Le script réécrit `corpus.jsonl` et imprime les compteurs par catégorie. Il ne
tourne **jamais** pendant les tests — c'est un outil de mainteneur, pas une
dépendance de la suite.

⚠ **Recapturer plus étroit désarmerait les balayages en silence.** Un corpus qui
aurait perdu ses `deleted`, son millésime inconnu ou ses arguments gabarités
rendrait les mêmes ✓ sur des questions qui ne seraient plus posées.
`test_le_corpus_porte_encore_les_formes_qu_il_doit_couvrir` est là pour ça, et
il est le premier de la série à dessein : il échoue **avant** les autres si la
matière a fondu. Après une recapture, relire les compteurs ci-dessus et les
mettre à jour dans le même commit — un tableau qui ne bouge pas quand le corpus
bouge atteste une couverture que personne n'a vérifiée.
