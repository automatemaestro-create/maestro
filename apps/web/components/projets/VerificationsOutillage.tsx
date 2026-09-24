/**
 * Le verdict de chaque commande que l'outillage écrit dans le projet (#1160).
 *
 * Maestro ne tire plus ses commandes d'une table sans les avoir jouées : chacune
 * est **jouée avant d'être écrite**, dans une copie du projet, et revient avec un
 * verdict — vérifiée, échouée avec son code et sa sortie, ou à vérifier avec sa
 * raison (`maestro.outillage.verification`). Ce composant est ce que la personne en
 * lit, et il répond à une question : **ce que Maestro vient d'écrire marche-t-il
 * vraiment — et sinon, quelle commande, pourquoi ?**
 *
 * ## La forme vient d'une veille et d'un choix rendu sur pièces
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1160 : trois
 * variantes rendues sur la vraie stack, jugées par un regard qui n'en était pas
 * l'auteur (#980). La retenue est **A — la liste de contrôle à plat**. Ce qu'elle
 * tranche, et qu'on ne défait pas sans rejouer le même geste :
 *
 * - **une ligne par commande, dans l'ordre où elle a été jouée** — d'après *GitHub
 *   Actions* (la page d'un job : une étape par ligne, l'installation d'abord). La
 *   variante qui mettait les exceptions en tête a été écartée : aucune référence ne
 *   réordonne, et les réussites condensées en une chaîne ne se distinguaient plus ;
 * - **le verdict porté par un glyphe ET un mot** — ✓ vérifiée, ✗ échouée, ⊖ à
 *   vérifier (la famille « cercle + signe » d'`Icones.tsx`), d'après *pre-commit.ci*
 *   (Passed / Failed / Skipped) : l'état ne tient jamais à la couleur seule ;
 * - **seul l'échec se déploie, code d'abord puis la fin de sa sortie**, sur place —
 *   d'après *pre-commit.ci* (« exit code: 1 », puis la sortie, sous la seule ligne
 *   en échec). La variante qui repliait la liste a été écartée sur pièces : la
 *   commande qui échoue et sa sortie passaient derrière un clic ;
 * - **ce qui n'a pas été joué dit pourquoi, sur sa ligne** — jamais une légende ;
 * - **la réussite en retrait** : « vérifiée » en ton neutre, comme GitHub Actions
 *   laisse ses réussites en gris ; l'échec seul porte la couleur d'alerte
 *   (correction du regard neuf).
 *
 * Et une correction de plus du regard neuf : le compte ne dit pas « N commandes
 * jouées » quand certaines ne l'ont pas été — il compte les commandes **écrites**,
 * puis leurs verdicts.
 *
 * **Au pied de la conversation**, la forme repliée est la bonne (#1104, et la
 * variante B écartée pour la page le disait) : `RecapitulatifVerifications` en rend
 * le compte en badges, et la liste se déplie sous le contrôle de la carte.
 */

import { Fragment } from "react";

import {
  IconeStatutAFaire,
  IconeStatutEchec,
  IconeStatutTerminee,
} from "@/components/Icones";
import { BadgeEtat, type Icone, type TonBadge } from "@/components/Primitives";
import type { VerificationOutillage } from "@/lib/types";

/** Un verdict, tel qu'il se lit : son mot, son ton, son glyphe. */
type Verdict = { libelle: string; pluriel: string; ton: TonBadge; icone: Icone };

/**
 * Les trois verdicts du moteur (`ETATS_VERIFICATION`), dans l'ordre où le compte les
 * dit. Un état inconnu — une API plus récente que l'écran — se lit « à vérifier » :
 * ne pas savoir ce qu'une commande vaut, c'est exactement ce que ce verdict dit.
 */
const VERDICTS: Record<string, Verdict> = {
  verifiee: {
    libelle: "vérifiée",
    pluriel: "vérifiées",
    ton: "neutre",
    icone: IconeStatutTerminee,
  },
  echouee: {
    libelle: "échouée",
    pluriel: "échouées",
    ton: "alerte",
    icone: IconeStatutEchec,
  },
  "a-verifier": {
    libelle: "à vérifier",
    pluriel: "à vérifier",
    ton: "attention",
    icone: IconeStatutAFaire,
  },
};

const ORDRE_DES_VERDICTS = ["verifiee", "echouee", "a-verifier"] as const;

function verdictDe(etat: string): Verdict {
  return VERDICTS[etat] ?? VERDICTS["a-verifier"];
}

function etatConnu(etat: string): string {
  return etat in VERDICTS ? etat : "a-verifier";
}

/** Combien de commandes portent chaque verdict — un état inconnu compte « à vérifier ». */
export function compterVerifications(
  verifications: VerificationOutillage[],
): Record<(typeof ORDRE_DES_VERDICTS)[number], number> {
  const compte = { verifiee: 0, echouee: 0, "a-verifier": 0 };
  for (const v of verifications) {
    compte[etatConnu(v.etat) as keyof typeof compte] += 1;
  }
  return compte;
}

/**
 * Un texte du moteur où les segments entre accents graves sont du code : la raison
 * d'un verdict nomme une commande ou un fichier (« `package.json` n'existe pas
 * encore »), et les accents graves bruts se lisaient comme des fautes (regard neuf).
 */
export function TexteAvecCode({ texte }: { texte: string }) {
  const morceaux = texte.split("`");
  // Un nombre impair d'accents graves laisse un segment ouvert : il reste du texte.
  const ferme = morceaux.length % 2 === 1;
  return (
    <>
      {morceaux.map((morceau, rang) =>
        rang % 2 === 1 && (ferme || rang < morceaux.length - 1) ? (
          <code key={rang} className="font-mono">
            {morceau}
          </code>
        ) : (
          <Fragment key={rang}>{rang % 2 === 1 ? `\`${morceau}` : morceau}</Fragment>
        ),
      )}
    </>
  );
}

/**
 * La phrase de compte : combien de commandes l'outillage écrit, et ce qu'elles valent.
 * L'échec seul en couleur d'alerte — la réussite reste en retrait.
 */
export function CompteVerifications({
  verifications,
}: {
  verifications: VerificationOutillage[];
}) {
  const compte = compterVerifications(verifications);
  const total = verifications.length;
  const morceaux = ORDRE_DES_VERDICTS.filter((etat) => compte[etat] > 0).map((etat) => {
    const verdict = VERDICTS[etat];
    const texte = `${compte[etat]} ${compte[etat] > 1 ? verdict.pluriel : verdict.libelle}`;
    return etat === "echouee" ? (
      <span key={etat} className="font-medium text-alerte-texte">
        {texte}
      </span>
    ) : (
      <span key={etat}>{texte}</span>
    );
  });
  return (
    <p className="text-corps text-texte">
      <strong>
        {total} commande{total > 1 ? "s" : ""}
      </strong>{" "}
      écrite{total > 1 ? "s" : ""} dans l&apos;outillage :{" "}
      {morceaux.map((morceau, rang) => (
        <Fragment key={rang}>
          {rang > 0 && ", "}
          {morceau}
        </Fragment>
      ))}
      .
    </p>
  );
}

/**
 * La liste de contrôle : une ligne par commande, dans l'ordre joué, et sous l'échec
 * son code puis la fin de sa sortie, sous « à vérifier » sa raison.
 */
export function ListeVerifications({
  verifications,
}: {
  verifications: VerificationOutillage[];
}) {
  return (
    <ul className="flex flex-col gap-2" aria-label="Verdict de chaque commande">
      {verifications.map((v) => {
        const etat = etatConnu(v.etat);
        const verdict = verdictDe(v.etat);
        return (
          <li key={v.commande} className="flex min-w-0 flex-col gap-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <BadgeEtat contour ton={verdict.ton} icone={verdict.icone}>
                {verdict.libelle}
              </BadgeEtat>
              <code className="min-w-0 font-mono text-annexe break-all text-texte">
                {v.commande}
              </code>
            </div>
            {etat === "echouee" && (
              <>
                <span className="text-annexe text-alerte-texte">
                  {v.code !== null ? `code ${v.code}` : "sans code de retour"}
                </span>
                {v.sortie !== "" && (
                  <pre className="max-h-40 overflow-auto rounded-controle border border-bord bg-surface-creuse p-2 font-mono text-micro whitespace-pre-wrap break-all text-texte">
                    {v.sortie}
                  </pre>
                )}
              </>
            )}
            {etat === "a-verifier" && v.raison !== "" && (
              <span className="text-annexe text-texte-secondaire">
                <TexteAvecCode texte={v.raison} />
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Le compte en badges chiffrés — le récapitulatif du pied de la conversation, où la
 * liste se déplie sous le contrôle de la carte (#1104).
 */
export function RecapitulatifVerifications({
  verifications,
}: {
  verifications: VerificationOutillage[];
}) {
  const compte = compterVerifications(verifications);
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {ORDRE_DES_VERDICTS.filter((etat) => compte[etat] > 0).map((etat) => {
        const verdict = VERDICTS[etat];
        return (
          <BadgeEtat key={etat} contour ton={verdict.ton} icone={verdict.icone}>
            {compte[etat]} {compte[etat] > 1 ? verdict.pluriel : verdict.libelle}
          </BadgeEtat>
        );
      })}
    </span>
  );
}
