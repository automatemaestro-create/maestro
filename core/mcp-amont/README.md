# core/mcp-amont — Miroir local du registre MCP officiel

Le miroir moissonné depuis
[`registry.modelcontextprotocol.io`](https://registry.modelcontextprotocol.io)
(ticket #675, lot 1/6 du parent #673). Lu et écrit par
[`maestro.agents.mcp_amont`](../../maestro/agents/mcp_amont.py) — **seul
écrivain**.

## Pourquoi un miroir plutôt qu'un appel direct

Le registre est en **préversion** et annonce lui-même « does not provide uptime
or data durability guarantees » ; sa doc d'*aggregator* demande de moissonner
« on a regular but infrequent basis (e.g. once per hour) » et de **persister
chez soi**
([registry-aggregators](https://modelcontextprotocol.io/registry/registry-aggregators)).

Mesuré le 2026-08-28, API interrogée en direct :

| Fait | Valeur |
|---|---|
| Taille du catalogue (`version=latest`) | **25 333 serveurs** |
| `limit` | plafonné à **100** (422 au-delà) |
| Pagination | `cursor` opaque, de la forme `<nom>:<version>` |
| Coût d'un aller | ~1,3 s |
| **Coût d'un moissonnage complet** | **254 pages, ~602 s** |

Dix minutes : ce n'est pas une requête synchrone dans une route d'API. Le miroir
n'est donc pas une optimisation de latence, c'est **la condition pour adosser un
écran à cette source**.

## Ce qu'il y a dans ce dossier

Deux fichiers, écrits atomiquement (tampon `.tmp` puis renommage) et **dans cet
ordre** — les données d'abord, l'état ensuite :

- `miroir.jsonl` — une entrée par ligne, triées par nom :

  ```jsonc
  {
    "nom": "io.modelcontextprotocol/everything",
    "version": "1.2.3",
    "description": "…",
    "statut": "active",              // active | deprecated | deleted (jamais stocké)
    "statut_change_le": "2026-07-27T10:44:51.359634Z",
    "publie_le": "2026-07-27T10:44:51.359634Z",
    "mis_a_jour_le": "2026-07-27T10:44:51.359634Z",
    "est_derniere": true,
    "document": { /* le server.json amont, VERBATIM */ }
  }
  ```

- `etat.json` — ce que le miroir sait de lui-même :

  ```jsonc
  {
    "amont": "https://registry.modelcontextprotocol.io",
    "rafraichi_le": "2026-08-28T12:00:00Z",   // notre horloge
    "moissonne_le": "2026-08-28T09:00:00Z",   // dernier moissonnage COMPLET
    "borne_amont": "2026-08-28T11:59:59Z",    // l'horloge de l'AMONT (filigrane)
    "nombre": 25333,
    "cause": "",                              // dernière cause d'échec, vidée au succès
    "echoue_le": ""
  }
  ```

L'ordre d'écriture est le contenu de la décision : une coupure entre les deux
laisse une borne **en retard**, donc un passage suivant qui redemande un peu
trop — idempotent. L'ordre inverse laisserait une borne en avance sur des
données jamais écrites, c'est-à-dire un trou définitif.

## Comment il se tient à jour

- **Premier passage** : moissonnage complet (`version=latest`, `limit=100`,
  curseur suivi jusqu'à épuisement). Il *remplace* le miroir.
- **Passages suivants** : `updated_since=<borne_amont>` avec
  `include_deleted=true` (que l'amont force de toute façon dans ce mode —
  mesuré : sur 100 entrées d'une fenêtre incrémentale, 96 `active`,
  1 `deprecated`, **3 `deleted`**, sans avoir passé le paramètre). Le résultat
  est *fusionné* par nom.
- **La borne vient de l'horloge de l'amont** — l'en-tête `Date` de la première
  page, moins une seconde —, jamais de la nôtre et jamais du `max(updatedAt)`
  vu. Le raisonnement complet est en tête de `maestro/agents/mcp_amont.py` ; en
  deux lignes : la pagination parcourt les noms dans l'ordre alphabétique, donc
  une entrée modifiée en début d'alphabet pendant qu'on lit la fin ne sera pas
  vue alors que son `updatedAt` reste *sous* le maximum de la passe — elle
  serait manquée pour toujours.
- **`status` est le seul champ mutable de l'amont** : une entrée passée
  `deprecated` **reste** dans le miroir, avec son statut (signalée, pas cachée) ;
  une entrée `deleted` **en sort** (politique de
  [modération](https://modelcontextprotocol.io/registry/moderation-policy) :
  spam, malware, illégal). Un statut inconnu — la préversion peut en ajouter —
  est conservé tel quel et l'entrée reste visible : seul `deleted` retire.
- **Périodicité** : `MAESTRO_MCP_AMONT_PERIODE`, défaut 1 h, planchée à 60 s.
  Un écran ne moissonne pas, il lit — `MiroirAmont.rafraichir_si_perime`
  constate d'abord.

## Ce qui arrive quand l'amont tombe

`MiroirAmont.rafraichir` **ne lève jamais**. Il rend un `Rafraichissement`
(`ok`, `cause`, compteurs), laisse le miroir précédent **intact** et persiste la
cause dans `etat.json` — pour qu'un écran ouvert trois heures après la panne
puisse la dire, au lieu d'afficher une fraîcheur qu'il ne peut pas justifier.
Trois familles nommées, parce qu'elles n'appellent pas le même geste : *amont
injoignable* (réseau, DNS, TLS), *amont trop lent* (délai d'un aller, ou budget
de la passe), *amont hors contrat* (statut HTTP, JSON illisible, enveloppe
inattendue, pagination qui boucle).

Un moissonnage complet qui rendrait **zéro** entrée alors que le miroir en
contient est rangé sous « hors contrat » et refusé : c'est la seule façon de
tenir « jamais un miroir vidé », un amont qui répond `{"servers": []}` ne se
distinguant pas d'un amont en panne par la seule lecture de sa réponse.

## Ce que ce miroir n'est pas

Ce n'est **pas** l'allowlist. Le garde-fou supply-chain de
[docs/19](../../docs/19-securite-modele-de-menace.md) — *découverte ≠
installation* — n'est pas levé : seule une entrée curée
(`maestro/agents/mcp_registry.py`, `SEED`) est instanciable. Le registre dit
« ce serveur existe », jamais « ce serveur est sûr ». La porte d'admission qui
fait passer une entrée découverte dans l'allowlist est le lot 4 du parent
(#678) ; la traduction `server.json` → entrée de bibliothèque est le lot 2
(#676).

Le `document` est stocké **verbatim** pour cette raison précise : un miroir qui
remodèle sa source est un miroir qu'il faut remoissonner — dix minutes — chaque
fois que le lecteur change d'avis, et le schéma amont est en préversion
(`2025-12-11`, et `2025-09-29` sur les entrées anciennes), donc il bougera.

Racine remplaçable par `MAESTRO_MCP_AMONT_DIR`, amont par
`MAESTRO_MCP_AMONT_URL` (cf. `.env.example`). Rien de ce dossier n'est commité
(voir `.gitignore`) : ce sont des données moissonnées par une machine, pas une
liste relue en revue de code.

Tests et doc de la phase : lot 6 du parent (#680).
