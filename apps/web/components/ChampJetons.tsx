"use client";

/**
 * Le champ à **jetons** (#256, lot 4/15 de #243) : une valeur qui est une liste
 * de mots courts, saisie mot à mot plutôt qu'en une chaîne à séparateurs.
 *
 * Ce qu'une chaîne virgulée ne peut pas faire, et qui est tout l'objet du lot :
 * montrer **ce qui a été retenu** (un jeton par mot, retirable un par un),
 * **proposer** le vocabulaire déjà en usage, et **signaler** un mot que rien ne
 * connaît — au lieu de l'accepter en silence au milieu d'une ligne de texte.
 *
 * ⚠ Il **suggère, il ne restreint pas** : la saisie reste libre, exactement
 * comme les champs « fournisseur » et « modèle » à côté (#487). Un vocabulaire
 * ne s'enrichit que si l'on peut y ajouter un mot ; une liste fermée rendrait
 * insaisissable tout ce que le catalogue ignore encore. Le signalement est donc
 * un **avertissement**, jamais un refus — le formulaire n'en est pas bloqué.
 *
 * ⚠ Le fichier est **à part de `Primitives.tsx`**, comme `BasculeDeVues` et
 * `Infobulle`, et pour la même raison : il appelle des hooks (`useId`,
 * `useState`), et `Primitives.tsx` est partagé avec des composants serveur.
 *
 * Trois points d'accessibilité à ne pas défaire :
 *
 * - **les jetons vivent hors du `<label>`.** Le cadre des primitives fait
 *   *entourer* le contrôle par son libellé (`Champ`, #535) ; on garde ce
 *   principe pour la saisie, mais les jetons — qui portent chacun un bouton —
 *   restent dehors : dedans, leur texte entrerait dans le nom accessible du
 *   champ (« Compétences react retirer css retirer ») et le clic sur « retirer »
 *   irait aussi au contrôle que le libellé désigne ;
 * - **la couleur ne porte jamais le signalement seule** : un jeton inédit
 *   affiche un glyphe et un mot lu par les lecteurs d'écran, pas seulement un
 *   fond ambre — la règle déjà tenue par `BadgeEtat` ;
 * - **chaque bouton déclare son plancher de 24 px** (`CIBLE_MINIMALE`, WCAG 2.2
 *   §2.5.8) : ce sont des cibles en petit corps, la famille même où le défaut a
 *   été mesuré (docs/30 §3.4).
 */

import { useId, useState, type KeyboardEvent, type ReactNode } from "react";

import { IconeAlerte, IconeFermer, IconePlus } from "@/components/Icones";
import {
  Bouton,
  CIBLE_MINIMALE,
  CLASSE_CONTROLE,
} from "@/components/Primitives";
import { decouperSaisie, normaliserCompetence } from "@/lib/competences";

/** Combien de suggestions se donnent à cliquer avant de renvoyer à la frappe. */
const SUGGESTIONS_MONTREES = 8;

export function ChampJetons({
  id,
  libelle,
  aide,
  avertissement,
  valeurs,
  onChange,
  suggestions = [],
  signales,
  motSignal = "inédit",
  nomElement = "l'élément",
  vide = "Rien pour l'instant : saisir un mot, puis Entrée.",
  desactive = false,
  placeholder,
  className = "",
}: {
  /** L'identifiant du contrôle : c'est lui qui rattache l'aide (même contrat que `Champ`). */
  id: string;
  libelle: ReactNode;
  /** Ce qu'il faut savoir avant de saisir — annoncé avec le champ. */
  aide?: ReactNode;
  /**
   * Ce qui mérite d'être signalé sans rien interdire — annoncé avec le champ,
   * lui aussi.
   *
   * ⚠ Ce n'est **pas** l'`erreur` de `Champ` (#535), et la différence est le
   * sujet du lot : une erreur pose `aria-invalid` et dit « ceci ne passera
   * pas ». Ici, la valeur passe — elle est seulement inhabituelle. Poser
   * `aria-invalid` sur un champ qu'on accepte serait annoncer un refus qui
   * n'arrivera pas.
   */
  avertissement?: ReactNode;
  valeurs: readonly string[];
  /** La liste entière après chaque geste : le champ ne mute jamais celle qu'il reçoit. */
  onChange: (valeurs: string[]) => void;
  /** Le vocabulaire proposé — à cliquer, et en complétion native à la frappe. */
  suggestions?: readonly string[];
  /** Les jetons à marquer : ici, ceux que le catalogue ne connaît pas. */
  signales?: ReadonlySet<string>;
  /** Le mot que porte un jeton marqué, pour qui ne voit pas la couleur. */
  motSignal?: string;
  /** Comment nommer un jeton dans les libellés d'action (« Retirer la compétence… »). */
  nomElement?: string;
  /** Ce que dit le champ quand il ne porte encore aucun jeton. */
  vide?: ReactNode;
  desactive?: boolean;
  placeholder?: string;
  /** Mise en page du bloc (largeur, colonne) — jamais une couleur. */
  className?: string;
}) {
  const [saisie, setSaisie] = useState("");
  const prefixe = useId();
  const idListe = `${prefixe}-vocabulaire`;

  const dejaLa = new Set(valeurs.map(normaliserCompetence));

  /** Ajoute ce qui est saisi (un mot, ou toute une liste collée) et vide la saisie. */
  function ajouter(brut: string) {
    // Les doublons sont écartés ici plutôt que laissés au dépôt : il les
    // dédoublonne bien (`_valide`), mais après un aller-retour réseau — le
    // jeton apparaîtrait deux fois à l'écran en attendant.
    const nouveaux = decouperSaisie(brut).filter((jeton) => !dejaLa.has(jeton));
    setSaisie("");
    if (nouveaux.length > 0) onChange([...valeurs, ...nouveaux]);
  }

  function retirer(valeur: string) {
    onChange(valeurs.filter((autre) => autre !== valeur));
  }

  function auClavier(evenement: KeyboardEvent<HTMLInputElement>) {
    if (evenement.key === "Enter" || evenement.key === ",") {
      // Entrée : sans ce `preventDefault`, la touche soumettrait le formulaire
      // qui entoure le champ au lieu de poser un jeton. La virgule : c'est le
      // séparateur d'avant ce lot, gardé pour ce qu'il reste d'habitude — mais
      // c'est le jeton qu'il valide, pas un caractère qu'il écrit.
      evenement.preventDefault();
      ajouter(saisie);
      return;
    }
    if (evenement.key === "Backspace" && saisie === "" && valeurs.length > 0) {
      evenement.preventDefault();
      retirer(valeurs[valeurs.length - 1]);
    }
  }

  // Ce qui reste à proposer : le vocabulaire moins ce qui est déjà posé. Borné,
  // et la borne se **dit** — une liste tronquée en silence se lit comme une
  // liste complète.
  const restantes = suggestions.filter((mot) => !dejaLa.has(mot));
  const proposees = restantes.slice(0, SUGGESTIONS_MONTREES);
  const enPlus = restantes.length - proposees.length;

  return (
    <div className={["flex flex-col gap-1.5", className].filter(Boolean).join(" ")}>
      <label className="flex flex-col gap-1">
        <span className="text-annexe font-medium text-texte-secondaire">
          {libelle}
        </span>
        <input
          id={id}
          type="text"
          value={saisie}
          onChange={(e) => setSaisie(e.target.value)}
          onKeyDown={auClavier}
          // Quitter le champ pose ce qui est écrit : sans ça, un mot tapé puis
          // abandonné pour cliquer « Enregistrer » serait perdu sans un mot —
          // et le bouton, qui compte les jetons, resterait éteint.
          onBlur={() => ajouter(saisie)}
          disabled={desactive}
          placeholder={placeholder}
          list={suggestions.length > 0 ? idListe : undefined}
          aria-describedby={
            [aide ? `${id}-aide` : "", avertissement ? `${id}-signal` : ""]
              .filter(Boolean)
              .join(" ") || undefined
          }
          className={CLASSE_CONTROLE}
        />
      </label>

      {suggestions.length > 0 && (
        // La complétion native à la frappe, en plus des suggestions à cliquer :
        // celles-ci sont bornées, celle-là porte le vocabulaire entier.
        <datalist id={idListe}>
          {suggestions.map((mot) => (
            <option key={mot} value={mot} />
          ))}
        </datalist>
      )}

      {valeurs.length > 0 ? (
        <ul className="flex flex-wrap gap-1">
          {valeurs.map((valeur) => {
            const marque = signales?.has(valeur) ?? false;
            return (
              <li key={valeur}>
                <span
                  className={
                    "inline-flex items-center gap-1 rounded-full py-0.5 pl-2 pr-1 text-annexe " +
                    (marque
                      ? "bg-attention-creux text-attention-texte"
                      : "bg-surface-creuse text-texte")
                  }
                >
                  {marque && (
                    <IconeAlerte aria-hidden="true" className="size-3.5 shrink-0" />
                  )}
                  {valeur}
                  {marque && <span className="sr-only"> — {motSignal}</span>}
                  <button
                    type="button"
                    onClick={() => retirer(valeur)}
                    disabled={desactive}
                    className={
                      `inline-flex ${CIBLE_MINIMALE} min-w-6 items-center justify-center ` +
                      "rounded-full text-annexe hover:bg-survol focus-visible:outline-2 " +
                      "focus-visible:outline-offset-1 focus-visible:outline-accent " +
                      "disabled:opacity-50"
                    }
                  >
                    <IconeFermer aria-hidden="true" className="size-3.5 shrink-0" />
                    <span className="sr-only">
                      Retirer {nomElement} « {valeur} »
                    </span>
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-annexe text-texte-secondaire">{vide}</p>
      )}

      {proposees.length > 0 && (
        <div className="flex flex-wrap items-baseline gap-1">
          <span className="text-annexe text-texte-secondaire">
            Déjà en usage :
          </span>
          {proposees.map((mot) => (
            // La primitive plutôt qu'un bouton à la main : c'est d'elle que
            // viennent le contour de focus et le plancher de 24 px, et de nulle
            // part ailleurs qu'on peut les promettre (#535, #269).
            <Bouton
              key={mot}
              variante="contour"
              ton="neutre"
              taille="petite"
              icone={IconePlus}
              disabled={desactive}
              onClick={() => ajouter(mot)}
            >
              {mot}
            </Bouton>
          ))}
          {enPlus > 0 && (
            <span className="text-annexe text-texte-secondaire">
              … et {enPlus} autre{enPlus > 1 ? "s" : ""}, à la frappe.
            </span>
          )}
        </div>
      )}

      {avertissement && (
        <div
          id={`${id}-signal`}
          className="flex flex-col gap-1 text-annexe text-attention-texte"
        >
          {avertissement}
        </div>
      )}

      {aide && (
        <p id={`${id}-aide`} className="text-annexe text-texte-secondaire">
          {aide}
        </p>
      )}
    </div>
  );
}
