"use client";

/**
 * **Le run a atteint son budget** — la carte du fil qui pose la question, et les
 * trois gestes qui y répondent (#1182).
 *
 * Avant ce ticket, un run qui atteignait son plafond de dépense jetait la tâche en
 * vol et refusait tout ce qui restait : l'écran se contentait de la cause
 * « Plafond de dépense atteint », sans geste pour en sortir. Le run se **suspend**
 * désormais (`maestro/engine/plafond.py`) : sa tâche coupée est mise de côté, rien
 * ne se dépense, et c'est ici que la personne décide — **relever** le plafond et
 * tout reprendre, **réduire** la portée en écartant ce qui peut attendre, ou
 * **arrêter** le run sur ce qui est fait.
 *
 * ## La forme vient d'une veille et d'un choix rendu sur pièces
 *
 * Commentaires « ## Veille de conception » et « ## Variante retenue » de #1182 —
 * cinq références (Vercel, GitHub Actions, Replit capturés ; Devin, Cursor lus),
 * trois variantes rendues sur la vraie stack contre un vrai run suspendu, jugées
 * par un regard qui n'en était pas l'auteur (#980, #1009). Retenue : **A**, un
 * seul formulaire, les chiffres en tuiles. Ce qu'elle tranche, et qu'on ne défait
 * pas sans rejouer le même geste :
 *
 * - **les trois volets de la question au même rang** — dépensé, reste à faire,
 *   coût du reste — en `TuileChiffre`, la dépense lue **contre le plafond**
 *   (Vercel, « $359 / 300 »). Aucune jauge : un anneau porterait l'état par sa
 *   forme et sa couleur, hors socle ;
 * - **le coût du reste est l'estimation du brief** (`lib/estimation`, docs/09),
 *   en fourchette et dite « ordre de grandeur » — jamais une autre estimation ;
 * - **réduire, c'est décocher dans la liste de ce qui reste** (GitHub Actions, où
 *   l'on choisit les environnements sur lesquels agir) ; la tâche coupée y est
 *   nommée « mise de côté — travail conservé » ;
 * - **le nouveau plafond se saisit dans le geste, prérempli** (Replit) — la
 *   dépense plus la borne haute de ce qui est gardé —, et **le bouton dit le
 *   montant qu'il engage** (GitHub, « I understand the consequences… ») : rien ne
 *   relève le plafond sans que la personne l'ait lu ;
 * - **la franchise** (Cursor) : ce qui était engagé au franchissement a pu passer
 *   un peu au-delà du plafond, et la carte le dit plutôt que de laisser croire à
 *   une borne qui ne déborde jamais.
 *
 * Écartées : **B** (trois options qui déplient leurs contrôles) — « réduire » tout
 * coché relevait le plafond sans le dire ; **C** (relevé façon grand livre) — des
 * fourchettes par tâche que l'estimation du brief ne fournit pas, et un badge
 * « 2213 % » qui faisait du dépassement le chiffre le plus fort.
 *
 * ⚠ **Aucune borne, donc aucun compte à rebours.** Le run attend sans limite : une
 * issue par défaut dépenserait ce que la personne n'a pas accordé, ou jetterait ce
 * qu'elle a payé. La carte le dit (« sans réponse, le run reste suspendu »).
 */

import { useId, useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeMonnaie } from "@/components/Icones";
import { BadgeEtat, Bouton, Champ, TuileChiffre } from "@/components/Primitives";
import { nomDuRun } from "@/lib/execution";
import {
  formatCout,
  formatFourchetteCout,
  formatHeureRelative,
  formatTokens,
} from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import {
  coutDuReste,
  decisionDeReprise,
  depenseEn,
  plafondPropose,
  unitesFranchies,
  type RunAuPlafond,
  type UnitePlafond,
} from "@/lib/plafond";
import { GESTE_ARRETER, type DecisionPlafond } from "@/lib/types";

/** Un montant dans l'unité de son plafond — dollars ou tokens. */
function enUnite(valeur: number, unite: UnitePlafond): string {
  return unite === "usd" ? formatCout(valeur) : `${formatTokens(valeur)} tokens`;
}

/** Le libellé du champ d'un nouveau plafond — l'unité **dans** le libellé. */
const LIBELLE_CHAMP: Record<UnitePlafond, string> = {
  usd: "Nouveau plafond, en $US",
  tokens: "Nouveau plafond, en tokens",
};

/** « 1 tâche », « 3 tâches » — le pluriel écrit, jamais « tâche(s) ». */
function taches(n: number): string {
  return `${n} tâche${n > 1 ? "s" : ""}`;
}

export function PlafondDansLeFil({
  run,
  trancher,
}: {
  run: RunAuPlafond;
  /** Porte la décision au moteur — relever, réduire ou arrêter. */
  trancher: (runId: string, decision: DecisionPlafond) => Promise<void>;
}) {
  const demande = run.plafond;
  const maintenant = useHorloge();
  const idCarte = useId();
  const restantes = demande.restantes;
  const unites = unitesFranchies(demande);

  const [gardees, setGardees] = useState<Set<string>>(
    () => new Set(restantes.map((tache) => tache.tache_id)),
  );
  // Ce que la personne a **écrit**, unité par unité. Tant qu'elle n'a rien écrit,
  // le champ suit le plafond proposé — qui suit ce qu'elle garde ; dès qu'elle
  // écrit, c'est son chiffre qui tient.
  const [saisis, setSaisis] = useState<Partial<Record<UnitePlafond, string>>>({});
  const [enCours, setEnCours] = useState<"reprendre" | "arreter" | null>(null);
  const [refus, setRefus] = useState<string | null>(null);

  const nbGardees = gardees.size;
  const nbEcartees = restantes.length - nbGardees;
  const reste = coutDuReste(nbGardees);

  const valeurs = Object.fromEntries(
    unites.map((unite) => {
      const propose = plafondPropose(demande, unite, nbGardees);
      const texte = saisis[unite] ?? (propose === null ? "" : String(propose));
      return [unite, texte];
    }),
  ) as Record<UnitePlafond, string>;

  // Un plafond se lit, puis se vérifie : un nombre, et au-delà de ce qui est déjà
  // dépensé — sinon le run retomberait dessus à sa première mesure (l'API le
  // refuse aussi, mais la personne l'apprend ici, avant d'avoir cliqué).
  const erreurs = Object.fromEntries(
    unites.map((unite) => {
      const texte = valeurs[unite].trim().replace(",", ".");
      if (texte === "") return [unite, "Écrivez le nouveau plafond."];
      const nombre = Number(texte);
      if (!Number.isFinite(nombre) || nombre <= 0) {
        return [unite, "Un montant positif, en chiffres."];
      }
      if (nombre <= depenseEn(demande, unite)) {
        return [
          unite,
          `Au-delà de ${enUnite(depenseEn(demande, unite), unite)}, déjà dépensés.`,
        ];
      }
      return [unite, null];
    }),
  ) as Record<UnitePlafond, string | null>;
  const montants = Object.fromEntries(
    unites.map((unite) => [
      unite,
      Number(valeurs[unite].trim().replace(",", ".")),
    ]),
  ) as Partial<Record<UnitePlafond, number>>;
  const saisieValide = unites.every((unite) => erreurs[unite] === null);
  const repriseImpossible = nbGardees === 0;

  const basculer = (tacheId: string) => {
    setGardees((avant) => {
      const suite = new Set(avant);
      if (suite.has(tacheId)) suite.delete(tacheId);
      else suite.add(tacheId);
      return suite;
    });
  };

  const envoyer = async (
    geste: "reprendre" | "arreter",
    decision: DecisionPlafond,
  ) => {
    if (enCours !== null) return;
    setEnCours(geste);
    setRefus(null);
    try {
      await trancher(run.run_id, decision);
      // Succès : le run sort de l'attente au rechargement et la carte se
      // démonte. En cas d'échec seulement, on rend la main pour réessayer.
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
      setEnCours(null);
    }
  };

  // Le montant que le bouton engage, dit en toutes lettres — la leçon de la
  // variante B écartée : un geste qui relève le plafond le nomme.
  const engage = unites
    .map((unite) => enUnite(montants[unite] ?? 0, unite))
    .join(" et ");
  const libelleReprise =
    nbEcartees === 0
      ? `Relever à ${engage} et reprendre`
      : `Reprendre sans ${taches(nbEcartees)} — plafond ${engage}`;

  const uniteAffichee: UnitePlafond = unites[0];
  const plafondActuel =
    uniteAffichee === "usd" ? demande.plafond_cout_usd : demande.plafond_tokens;

  return (
    <CarteDuFil
      libelle="Budget du run atteint"
      titre="Budget du run atteint"
      icone={IconeMonnaie}
      aside={
        run.attente_depuis ? (
          <span className="text-annexe text-texte-secondaire">
            suspendu {formatHeureRelative(run.attente_depuis, maintenant)}
          </span>
        ) : undefined
      }
    >
      {/* Quel run — deux runs d'un même projet peuvent attendre ensemble, et la
          carte de l'un ne doit pas se lire comme celle de l'autre. */}
      <p className="mb-3 text-annexe text-texte-secondaire">{nomDuRun(run)}</p>
      <p className="text-titre font-semibold text-texte">
        Le run a atteint son plafond de dépense. Relever, réduire ou arrêter ?
      </p>

      {/* Les trois volets de la question, au même rang. Un conteneur à lui : la
          carte vit au pied de `/chat` comme dans la colonne de conversation, et
          c'est sa largeur à elle qui décide des colonnes, pas celle de l'écran.
          La troisième tuile est plus large : c'est elle qui porte une fourchette. */}
      <div className="@container mt-3">
        <div className="grid grid-cols-1 gap-3 @xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)]">
          <TuileChiffre
            libelle="Dépensé"
            valeur={
              uniteAffichee === "usd"
                ? formatCout(demande.depense_usd)
                : `${formatTokens(demande.depense_tokens)} tokens`
            }
            detail={
              plafondActuel === null
                ? undefined
                : `sur un plafond de ${enUnite(plafondActuel, uniteAffichee)}`
            }
          />
          <TuileChiffre
            libelle="Reste à faire"
            valeur={taches(nbGardees)}
            detail={
              nbEcartees > 0
                ? `${taches(nbEcartees)} écartée${nbEcartees > 1 ? "s" : ""}`
                : "tout ce que le plan prévoyait"
            }
          />
          <TuileChiffre
            libelle="Coût estimé du reste"
            valeur={formatFourchetteCout(reste.bas, reste.haut)}
            detail="ordre de grandeur, pas une mesure"
          />
        </div>
      </div>

      <fieldset className="mt-4">
        <legend className="text-annexe font-medium text-texte-secondaire">
          Ce qui reste à faire — décochez ce qui peut attendre
        </legend>
        <ul className="mt-2 flex flex-col gap-1.5">
          {restantes.map((tache) => {
            const gardee = gardees.has(tache.tache_id);
            const idCase = `plafond-${idCarte}-${tache.tache_id}`;
            return (
              <li key={tache.tache_id}>
                <label
                  htmlFor={idCase}
                  className="grid cursor-pointer grid-cols-[auto_1fr] items-start gap-x-2"
                >
                  <input
                    id={idCase}
                    type="checkbox"
                    checked={gardee}
                    disabled={enCours !== null}
                    onChange={() => basculer(tache.tache_id)}
                    className="mt-0.5 size-4 shrink-0 rounded-controle border-bord-fort"
                  />
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    {/* Écartée : barrée **et** grisée — deux signaux, jamais la
                        couleur seule (même règle que `LigneRole`). */}
                    <span
                      className={
                        gardee
                          ? "text-corps text-texte"
                          : "text-corps text-texte-secondaire line-through"
                      }
                    >
                      {tache.titre}
                    </span>
                    {tache.interrompue && (
                      <BadgeEtat ton="info" contour>
                        mise de côté — travail conservé
                      </BadgeEtat>
                    )}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>

      {!repriseImpossible && (
        <div className="mt-4 flex flex-wrap gap-3">
          {unites.map((unite) => (
            <Champ
              key={unite}
              id={`plafond-${idCarte}-${unite}`}
              className="w-full max-w-56"
              libelle={LIBELLE_CHAMP[unite]}
              inputMode="decimal"
              value={valeurs[unite]}
              onChange={(e) =>
                setSaisis((avant) => ({ ...avant, [unite]: e.target.value }))
              }
              disabled={enCours !== null}
              aide={
                unite === "usd"
                  ? `Proposé : ce qui est dépensé, plus le haut de l'estimation pour ${taches(nbGardees)}.`
                  : `Au-delà des ${formatTokens(demande.depense_tokens)} tokens déjà dépensés — l'estimation est en dollars, le plafond en tokens s'écrit.`
              }
              erreur={erreurs[unite] ?? undefined}
            />
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {repriseImpossible ? (
          <p className="text-annexe text-texte-secondaire">
            Rien n&apos;est gardé : le run ne peut que s&apos;arrêter.
          </p>
        ) : (
          <Bouton
            disabled={!saisieValide || enCours !== null}
            occupe={enCours === "reprendre"}
            onClick={() =>
              void envoyer("reprendre", decisionDeReprise(demande, gardees, montants))
            }
          >
            {libelleReprise}
          </Bouton>
        )}
        <Bouton
          variante="contour"
          ton="neutre"
          disabled={enCours !== null}
          occupe={enCours === "arreter"}
          onClick={() => void envoyer("arreter", { geste: GESTE_ARRETER })}
        >
          Arrêter le run
        </Bouton>
      </div>

      <p className="mt-3 text-annexe text-attention-texte">
        Sans réponse, le run reste suspendu : rien ne se dépense. Ce qui était déjà
        engagé au franchissement a pu dépasser un peu le plafond — un appel modèle
        ne se tarife qu&apos;une fois fait.
      </p>
      {refus !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
    </CarteDuFil>
  );
}
