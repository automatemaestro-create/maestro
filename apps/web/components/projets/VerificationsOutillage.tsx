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
 * - **ce qui n'a pas été joué dit pourquoi, sur sa ligne** — jamais une légende,
 *   **sauf** une raison que toutes les commandes « à vérifier » partagent : elle se
 *   dit alors une fois, en tête de liste (`raisonCommune`). Amendement consigné sur
 *   le ticket (second « Variante retenue » de #1160) après la relecture : cinq lignes
 *   identiques sous un projet neuf se lisaient comme cinq problèmes ;
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
 * Une raison du moteur en phrase : majuscule en tête, ponctuation finale. Le moteur les
 * écrit en minuscule pour qu'elles s'insèrent dans `AGENTS.md` après « À vérifier : »
 * ; seules, elles se lisaient sans majuscule ni point à côté d'une autre qui en avait
 * (regard neuf de #1160).
 */
export function enPhrase(raison: string): string {
  const nette = raison.trim();
  if (nette === "") return nette;
  return ponctuee(nette.charAt(0).toLocaleUpperCase("fr") + nette.slice(1));
}

/**
 * Une raison ponctuée sans changer sa casse — celle qui suit un deux-points —, ses
 * guillemets français tenus à leur mot par une espace insécable : à 277 px, « « »
 * restait seul en fin de ligne (second regard neuf de #1160).
 */
export function ponctuee(raison: string): string {
  const nette = raison.trim().replace(/« /g, "« ").replace(/ »/g, " »");
  if (nette === "") return nette;
  return /[.!?…]$/.test(nette) ? nette : `${nette}.`;
}

/**
 * La raison que **toutes** les commandes « à vérifier » partagent, s'il y en a au moins
 * deux — `null` sinon. Dite alors une fois, en tête de liste, plutôt que répétée à
 * l'identique sous chaque ligne : cinq fois la même phrase sous un projet neuf se
 * lisaient comme cinq problèmes (regard neuf de #1160). Des raisons qui diffèrent
 * restent chacune sur sa ligne.
 */
export function raisonCommune(verifications: VerificationOutillage[]): string | null {
  const raisons = verifications
    .filter((v) => etatConnu(v.etat) === "a-verifier")
    .map((v) => v.raison.trim());
  if (raisons.length < 2 || raisons[0] === "") return null;
  return raisons.every((r) => r === raisons[0]) ? raisons[0] : null;
}

/**
 * La liste de contrôle : une ligne par commande, dans l'ordre joué, et sous l'échec
 * son code puis la fin de sa sortie, sous « à vérifier » sa raison.
 *
 * Une **sous-grille** aligne toutes les commandes sur la même verticale, quelle que
 * soit la largeur de leur badge, et garde badge et commande sur la même ligne jusque
 * dans la colonne de conversation : à 277 px, un badge seul sur sa ligne s'empilait
 * sous le récapitulatif (regard neuf de #1160). Le détail d'une ligne prend toute la
 * largeur, pour qu'une sortie ne tienne pas dans une demi-colonne.
 */
export function ListeVerifications({
  verifications,
}: {
  verifications: VerificationOutillage[];
}) {
  const commune = raisonCommune(verifications);
  return (
    <div className="flex flex-col gap-2">
      {commune !== null && (
        <p className="text-annexe text-texte-secondaire">
          Les {compterVerifications(verifications)["a-verifier"]} commandes à
          vérifier, pour une même raison : <TexteAvecCode texte={ponctuee(commune)} />
        </p>
      )}
      <ul
        className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-2 gap-y-2"
        aria-label="Verdict de chaque commande"
      >
        {verifications.map((v) => {
          const etat = etatConnu(v.etat);
          const verdict = verdictDe(v.etat);
          return (
            <li
              key={v.commande}
              className="col-span-2 grid grid-cols-subgrid items-center gap-y-1"
            >
              <BadgeEtat contour ton={verdict.ton} icone={verdict.icone}>
                {verdict.libelle}
              </BadgeEtat>
              {/* `break-words` : une commande se replie entre ses mots, et seul un mot
                  plus long que la colonne se coupe (« require / ments.txt » à 277 px). */}
              <code className="min-w-0 font-mono text-annexe break-words text-texte">
                {v.commande}
              </code>
              {etat === "echouee" && (
                <div className="col-span-2 flex min-w-0 flex-col gap-1">
                  <span className="text-annexe text-alerte-texte">
                    {v.code !== null ? `code ${v.code}` : "sans code de retour"}
                  </span>
                  {v.sortie !== "" && <FinDeSortie sortie={v.sortie} />}
                </div>
              )}
              {etat === "a-verifier" && v.raison !== "" && commune === null && (
                <span className="col-span-2 text-annexe text-texte-secondaire">
                  <TexteAvecCode texte={enPhrase(v.raison)} />
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Les lignes de sortie montrées sous un échec : **la fin**, là où une commande dit
 * pourquoi elle a échoué — le moteur en garde davantage au manifeste.
 */
export const LIGNES_DE_SORTIE = 12;

/**
 * La fin de la sortie d'une commande en échec, **sans zone qui défile** : une boîte
 * défilante devrait être atteignable au clavier (WCAG 2.1.1, le constat
 * d'`EditeurPlaybook`), et une sortie de 4 000 caractères pousserait le rapport
 * hors de l'écran. On en montre donc les dernières lignes, les lignes longues
 * repliées sur place, et l'on dit où lire le reste.
 */
function FinDeSortie({ sortie }: { sortie: string }) {
  const lignes = sortie.split("\n");
  const coupee = lignes.length > LIGNES_DE_SORTIE;
  const visibles = lignes.slice(-LIGNES_DE_SORTIE).join("\n");
  return (
    <>
      {/* `break-words` et non `break-all` : à 320 px, `break-all` coupait les mots de
          la sortie en plein milieu (« blank li / nes », relecture de #1160) ; ici seul
          un mot plus long que la ligne se coupe. */}
      <pre className="rounded-carte border border-bord bg-surface-creuse p-2.5 font-mono text-micro whitespace-pre-wrap break-words text-texte">
        {coupee ? `…\n${visibles}` : visibles}
      </pre>
      {coupee && (
        <span className="text-micro text-texte-secondaire">
          Les {LIGNES_DE_SORTIE} dernières lignes — la sortie gardée est dans{" "}
          <code className="font-mono">.maestro/outillage/manifeste.json</code>.
        </span>
      )}
    </>
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
