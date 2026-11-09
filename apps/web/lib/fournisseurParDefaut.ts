/**
 * Ce que l'écran du fournisseur par défaut tire des données de l'API (#1609) —
 * sans rien en deviner.
 *
 * Trois lectures, et une règle : **présent n'est pas éprouvé**. La sonde du poste
 * dit ce qu'elle a vu (un CLI sur le `PATH`, un serveur qui répond, une clé dans
 * l'environnement) ; seule l'épreuve dit qu'un accès **marche**. L'écran ne
 * colore donc jamais en vert ce que la sonde a vu, et ne rend le verdict que là
 * où l'épreuve l'a établi.
 *
 * Les phrases d'un verdict sont écrites **par motif**, et le motif est lu dans des
 * champs typés de la réponse du fournisseur (`maestro.providers.base.Epreuve`) :
 * aucun texte d'erreur n'est relu ici.
 */

import type {
  AccesFournisseur,
  CatalogueFournisseurs,
  ConstatPoste,
  DemandeFournisseurDefaut,
  Epreuve,
  EtatFournisseurDefaut,
  FournisseurCatalogue,
} from "./types";

/** Un accès, avec le fournisseur qui le déclare : une carte de l'écran. */
export type OptionAcces = {
  /** `<fournisseur>:<accès>` — unique sur l'écran, l'id d'un accès ne l'est pas. */
  cle: string;
  fournisseur: FournisseurCatalogue;
  acces: AccesFournisseur;
};

/** Toutes les cartes, dans l'ordre du registre puis des accès qu'il déclare. */
export function optionsAcces(catalogue: CatalogueFournisseurs): OptionAcces[] {
  return catalogue.fournisseurs.flatMap((fournisseur) =>
    fournisseur.acces.map((acces) => ({
      cle: cleOption(fournisseur.nom, acces.id),
      fournisseur,
      acces,
    })),
  );
}

export function cleOption(fournisseur: string, acces: string): string {
  return `${fournisseur}:${acces}`;
}

/**
 * Ce que la sonde a vu de cet accès : les constats que ses témoins désignent.
 * Une liste vide dit « rien de détecté », jamais « absent » — la sonde ne voit
 * que le `PATH` et l'environnement du process qui sert l'API.
 */
export function constatsDe(option: OptionAcces): ConstatPoste[] {
  return option.fournisseur.constats.filter((constat) =>
    option.acces.temoins.includes(constat.cle),
  );
}

/**
 * La carte de ce que l'exécution utilise **maintenant** — ou null quand
 * l'effectif ne désigne aucun accès connu (un fournisseur hors registre).
 */
export function cleEffective(etat: EtatFournisseurDefaut): string | null {
  const { fournisseur, acces } = etat.effectif;
  return acces === null ? null : cleOption(fournisseur, acces);
}

/** La carte du choix enregistré, ou null quand rien ne l'est. */
export function cleEnregistree(etat: EtatFournisseurDefaut): string | null {
  return etat.choix === null ? null : cleOption(etat.choix.fournisseur, etat.choix.acces);
}

/**
 * Ce qui identifie un choix saisi : une épreuve ne vaut que pour la saisie
 * qu'elle a éprouvée. Changer un champ efface son verdict — la clé n'y entre que
 * par sa présence, l'empreinte ne se garde pas.
 */
export function empreinteDemande(demande: DemandeFournisseurDefaut): string {
  return JSON.stringify([
    demande.fournisseur,
    demande.acces,
    demande.modele ?? "",
    demande.adresse ?? "",
    demande.cle ?? "",
  ]);
}

/** Les trois sortes d'accès, que le verdict raconte chacune à sa façon. */
type SorteAcces = "outil" | "serveur" | "cle";

function sorteDe(acces: AccesFournisseur): SorteAcces {
  if (acces.secret !== null) return "cle";
  if (acces.adresse !== null) return "serveur";
  return "outil";
}

/** Le programme que l'accès lance, quand la sonde le nomme (`cli:<commande>`). */
function commandeDe(acces: AccesFournisseur): string | null {
  const temoin = acces.temoins.find((cle) => cle.startsWith("cli:"));
  return temoin === undefined ? null : temoin.slice("cli:".length);
}

/**
 * Ce que le verdict dit à la personne : ce qui s'est passé, et le geste qui le
 * répare. Une phrase par motif, jamais un message d'erreur relu.
 */
export function phraseDuVerdict(epreuve: Epreuve, acces: AccesFournisseur): string {
  const sorte = sorteDe(acces);
  const commande = commandeDe(acces);
  switch (epreuve.motif) {
    case "ok":
      return "L'accès répond : un tour de modèle a abouti.";
    case "outil-absent":
      return commande === null
        ? "Le programme de cet accès est introuvable sur ce poste."
        : `Le programme « ${commande} » est introuvable sur ce poste. Installez-le, ou choisissez un autre accès.`;
    case "authentification":
      if (sorte === "outil") {
        return commande === null
          ? "L'accès n'est pas connecté à un compte, ou sa connexion a expiré."
          : `« ${commande} » n'est pas connecté à un compte, ou sa connexion a expiré. Lancez « ${commande} auth login » dans un terminal, puis éprouvez de nouveau.`;
      }
      if (sorte === "cle") return "La clé est refusée. Vérifiez-la, ou collez-en une autre.";
      return "Le serveur refuse l'accès : il demande sans doute une clé.";
    case "facturation":
      return "Le compte n'a plus de crédit, ou son abonnement n'est plus actif.";
    case "limite":
      return "La limite d'usage du compte est atteinte pour le moment. Réessayez plus tard.";
    case "requete":
      return sorte === "outil"
        ? "La demande est refusée : ce modèle n'est sans doute pas ouvert à ce compte."
        : "La demande est refusée : ce modèle n'est sans doute pas servi à cette adresse.";
    case "injoignable":
      return "Rien ne répond à cette adresse. Le serveur est-il démarré ?";
    case "serveur":
      return "Le service répond en erreur. Réessayez dans un moment.";
    case "inconnu":
      return "L'épreuve a échoué sans en donner la raison.";
  }
}
