/**
 * Ce que la section « Général » dit de l'installation (#1622) : la version de
 * Maestro, et le CLI `claude` qu'il lance.
 *
 * Chaque ligne se compose des champs typés de `GET /api/installation` — la
 * forme du produit, la provenance du CLI, les versions lues —, jamais d'un
 * texte à interpréter. Une ligne qui signale ce qui ne suit pas l'installation
 * porte `ecart` : l'écran la rend alors comme l'épreuve en échec d'un
 * fournisseur (#1609), une alerte avec le motif et le geste, à l'endroit de la
 * propriété.
 *
 * Une version qui ne se lit pas reste « illisible » : elle n'est jamais devinée,
 * ni de celle du SDK, ni d'une valeur passée.
 */

import type { EtatInstallation } from "./types";

export type LigneInstallation = {
  /** Ce qui s'affiche en regard du libellé : une version, ou l'état qui en tient lieu. */
  valeur: string;
  /** `true` quand `valeur` est une version lue (rendue en chasse fixe). */
  estVersion: boolean;
  /** Le motif, et le geste quand il y en a un à faire. */
  phrase: string;
  /** La phrase signale un écart avec l'installation. */
  ecart: boolean;
  /** Un relevé ou un chemin, en chasse fixe sous la phrase — `null` sans objet. */
  detail: string | null;
};

/** Le geste d'une installation dont une partie manque ou ne la suit pas. */
export const GESTE_REINSTALLER =
  "Réinstallez Maestro : son installeur pose le produit et son CLI ensemble.";

/** Dans un clone, le CLI embarqué est celui du SDK installé dans le venv. */
export const GESTE_SDK_DU_DEPOT =
  "Dans un clone, c'est le SDK du venv qui l'embarque : réinstallez claude-agent-sdk dans .venv.";

/** La version de Maestro qui tourne ici. */
export function ligneVersion(etat: EtatInstallation): LigneInstallation {
  const { version, releve } = etat.produit;
  if (version !== null) {
    return {
      valeur: version,
      estVersion: true,
      phrase:
        etat.forme === "installation"
          ? "Celle de cette installation, écrite quand son paquet a été construit."
          : "Ce Maestro tourne depuis un clone du dépôt : c'est la version que porterait son paquet.",
      ecart: false,
      detail: null,
    };
  }
  return {
    valeur: "Illisible",
    estVersion: false,
    phrase:
      etat.forme === "installation"
        ? `Cette installation ne dit pas sa version. ${GESTE_REINSTALLER}`
        : "Le dépôt ne donne pas sa version : apps/desktop/package.json doit la porter.",
    ecart: true,
    detail: releve === "" ? null : releve,
  };
}

/** Le CLI `claude` que Maestro lance, et ce qu'il y a à faire s'il ne suit pas l'installation. */
export function ligneCli(etat: EtatInstallation): LigneInstallation {
  const { provenance, chemin, version } = etat.cli_claude;
  const geste = etat.forme === "installation" ? GESTE_REINSTALLER : GESTE_SDK_DU_DEPOT;
  const valeur = version ?? "Version illisible";
  const estVersion = version !== null;

  if (provenance === "absent") {
    return {
      valeur: "Introuvable",
      estVersion: false,
      phrase: `Ni le CLI que Maestro embarque, ni un claude sur le PATH : aucun agent ne peut démarrer. ${geste}`,
      ecart: true,
      detail: null,
    };
  }
  if (provenance === "poste") {
    return {
      valeur,
      estVersion,
      phrase:
        version === null
          ? `Pris sur le poste (PATH), hors de l'installation, et il n'a pas dit sa version. ${geste}`
          : `Pris sur le poste (PATH), hors de l'installation : sa version ne suit pas celle de Maestro. ${geste}`,
      ecart: true,
      detail: chemin,
    };
  }
  if (version === null) {
    return {
      valeur,
      estVersion,
      phrase: `Le CLI que Maestro embarque n'a pas dit sa version : il ne démarre peut-être plus. ${geste}`,
      ecart: true,
      detail: chemin,
    };
  }
  return {
    valeur,
    estVersion,
    phrase: "Celui que Maestro embarque avec son SDK : il suit l'installation.",
    ecart: false,
    detail: chemin,
  };
}

/**
 * Les deux lignes quand l'API ne les a pas rendues : rien n'est supposé à leur
 * place. Le relevé est le message de la lecture en échec (« … n'a pas répondu »,
 * « … a répondu 503 »), et « Tester la connexion » dit si l'API répond.
 */
export function lignesSansApi(releve: string): [LigneInstallation, LigneInstallation] {
  return [
    {
      valeur: "Illisible",
      estVersion: false,
      phrase:
        // Espaces insécables dans les guillemets : un « ne finit pas une ligne seul.
        "La version se lit dans l'API, qui ne l'a pas rendue : « Tester la connexion », plus bas, dit si elle répond.",
      ecart: true,
      detail: releve,
    },
    {
      valeur: "Illisible",
      estVersion: false,
      phrase: "Il se lit dans l'API, comme la version : sans elle, rien n'est supposé.",
      ecart: true,
      detail: null,
    },
  ];
}
