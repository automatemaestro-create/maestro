# core/secrets — Coffre local des secrets par agent

Coffre des **secrets par agent** (ticket #109, parent #102) : les tokens des
intégrations MCP (Slack #105, GitLab #106…) sortent de l'environnement global
du process — chaque agent ne voit que **ses** secrets.

**Rien d'autre que ce README n'est versionné ici** (voir `.gitignore`) : un
coffre contient des secrets réels, il reste local au poste ou au worker.

## Fonctionnement

- Un fichier par agent : `<agent>.json`, de la forme
  `{"secrets": {"VARIABLE": <entrée>}}` — les noms de variables sont ceux que
  référencent les déclarations MCP de l'agent (`${VARIABLE}`,
  [core/mcp/README.md](../mcp/README.md)).
- **Chiffrement au repos (#132)** : un secret n'est **jamais posé en clair**
  dans le fichier. Il est chiffré (Fernet, AES-128-CBC + HMAC-SHA256) avec la
  clé maîtresse **côté serveur** — `MAESTRO_SECRETS_KEY` (clé Fernet, cf.
  `.env.example`) si elle est posée, sinon une clé locale auto-générée
  (`<racine>/.cle`, gitignorée) : le repli du POC. En V1, la clé passera à un
  vrai gestionnaire de secrets (Vault, KMS…) sans changer ce contrat. Le format
  hérité `{"secrets": {"VAR": "valeur en clair"}}` **reste lu** (rétro-compat).
- **Trois modes d'auth (#132, [docs/21 §3.2](../../docs/21-configuration-mcp.md))**,
  posés par l'UI de configuration (lot #133) via `SecretStore.enregistrer` :
  - **token statique** (`token_statique`) : un vrai secret (PAT GitLab, token de
    bot Slack), chiffré au repos ;
  - **appairage** (`appairage`) : une valeur **éphémère non secrète** (canal du
    pont Figma communautaire), stockée en clair et **non** masquée — jetable, à
    renouveler à chaque session de plugin ;
  - **token OAuth importé** (`oauth_importe`) : chiffré et **expirable** ; une
    échéance dépassée rend le serveur **refusé au montage**, et le
    renouvellement est une ré-importation (`SecretStore.renouveler`). L'état de
    validité se lit sans déchiffrer (`SecretStore.etat`).
- **Opt-in par provisionnement** : tant qu'aucun coffre `<agent>.json`
  n'existe ici (ce README ne compte pas), la résolution des références
  `${VAR}` garde l'environnement du process (comportement historique #104).
  Dès le **premier coffre écrit**, le scoping est **strict pour tous les
  agents** : chacun ne résout que dans son coffre — un secret absent rend le
  serveur indisponible (échec propre), même si la variable traîne dans le
  shell. Migrez donc **tous** les agents à intégrations d'un coup.
- **Masquage automatique** : toute valeur **secrète** servie par un coffre (ou
  résolue via `${VAR}`) est enregistrée au registre de rédaction
  (`maestro.telemetry.redact`) — masquée si elle réapparaît dans un journal,
  une trace Langfuse, un rapport ou un ticket. Une valeur d'appairage (non
  secrète) ne l'est pas.
- Relu **à chaud** à chaque tâche (comme les déclarations MCP) : tourner un
  secret, ou un token qui expire, vaut pour la tâche suivante, sans redémarrage.
- Racine remplaçable par `MAESTRO_SECRETS_DIR` (cf. `.env.example`). En mode
  distribué, workers et moteur doivent voir le même stockage **et la même clé**.

## Forme stockée (entrée structurée)

Les valeurs ci-dessous sont des **exemples de forme**, pas de vrais secrets —
un vrai coffre n'est jamais versionné.

```json
{
  "secrets": {
    "GITLAB_TOKEN": {"mode_auth": "token_statique", "secret": true, "chiffre": "gAAAAA…"},
    "FIGMA_CHANNEL": {"mode_auth": "appairage", "secret": false, "valeur": "abc-123"},
    "FIGMA_OAUTH_TOKEN": {
      "mode_auth": "oauth_importe", "secret": true, "chiffre": "gAAAAA…",
      "expire_le": "2026-08-01T12:00:00+00:00"
    }
  }
}
```

Détail : [docs/18-secrets-par-agent.md](../../docs/18-secrets-par-agent.md).
Tests exhaustifs différés → lot 5/5 (#134) ; la logique de chiffrement et
d'expiration, critique, est couverte dès ce lot (`tests/test_secrets_chiffrement.py`).
