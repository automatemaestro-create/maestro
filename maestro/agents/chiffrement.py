"""Chiffrement au repos des secrets d'intégration (ticket #132, parent #102).

Petit primitif de chiffrement **symétrique authentifié** (Fernet — AES-128-CBC +
HMAC-SHA256, bibliothèque `cryptography`) : le coffre des secrets
(`maestro.agents.secrets`) stocke désormais les tokens **chiffrés** sur disque au
lieu de les poser en clair. La déclaration MCP versionnée n'a jamais porté de
secret (références `${VAR}`, #104) ; ce module ferme l'autre moitié — le fichier
local du coffre (`core/secrets/<agent>.json`, gitignoré mais susceptible de
fuiter par une sauvegarde, une copie ou un collage) ne contient plus de valeur
lisible sans la clé.

La clé maîtresse vit **côté serveur, hors du dépôt** :

- `MAESTRO_SECRETS_KEY` (clé Fernet urlsafe base64) si elle est posée dans
  l'environnement — la forme recommandée, dérivée en V1 d'un vrai gestionnaire de
  secrets (Vault, KMS…) ;
- sinon, une clé locale **auto-générée** par le coffre (`<racine>/.cle`,
  gitignorée avec le reste du coffre) — le repli du POC, pour que la
  fonctionnalité marche sans réglage préalable. La clé posée à côté du chiffré ne
  protège pas contre un attaquant qui a le disque entier ; mais l'indirection est
  en place (chiffré au repos, clé nommée), et elle passera à un KMS sans changer
  ce contrat.

`InvalidToken` (clé erronée, données altérées) est mué en `ValueError` avec sa
cause : on ne déchiffre jamais depuis un coffre douteux — le même contrat de
« validation à la lecture » que le reste du socle MCP (#104/#109).
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class Chiffreur:
    """Chiffre/déchiffre une chaîne avec une clé Fernet (AES-128-CBC + HMAC-SHA256).

    Construit sur une clé Fernet (`generer_cle` pour en forger une neuve) — une
    clé malformée est refusée dès la construction (`ValueError`), jamais au
    premier chiffrement. Le jeton produit par `chiffrer` est du texte ASCII
    (base64 urlsafe), donc directement JSON-sérialisable dans le fichier du coffre.
    """

    def __init__(self, cle: bytes | str) -> None:
        # Fernet valide la clé (32 octets base64 urlsafe) à la construction et
        # lève ValueError si elle est malformée : on remonte la même exception,
        # avec un message qui nomme la cause probable (clé tronquée/mal copiée).
        try:
            self._fernet = Fernet(cle)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "clé de chiffrement invalide (clé Fernet urlsafe base64, 32 "
                "octets attendue) — vérifiez MAESTRO_SECRETS_KEY."
            ) from exc

    @staticmethod
    def generer_cle() -> bytes:
        """Une nouvelle clé Fernet (urlsafe base64, 32 octets) — à garder secrète."""
        return Fernet.generate_key()

    def chiffrer(self, clair: str) -> str:
        """Le jeton chiffré (texte ASCII, JSON-sérialisable) de la valeur `clair`."""
        return self._fernet.encrypt(clair.encode("utf-8")).decode("ascii")

    def dechiffrer(self, jeton: str) -> str:
        """La valeur en clair du `jeton`, ou `ValueError` si clé erronée / données altérées."""
        try:
            return self._fernet.decrypt(jeton.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "déchiffrement impossible : clé de chiffrement erronée ou "
                "valeur du coffre altérée."
            ) from exc
