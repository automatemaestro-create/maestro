"use client";

/**
 * **L'annonce de fin d'un run** (#928, lot 7 de #921) — ce qu'on lit quand le
 * travail est fini, et le geste qui mène à ce qu'il a produit.
 *
 * Deux surfaces la montent, et c'est pourquoi elle vit ici plutôt que dans
 * l'une des deux : le **fil** (`components/Conversation`, à l'heure de la fin,
 * sous le récit du run quand il y en a un — `rangDeLaFin`, #1290) et la **cloche**
 * (`components/CentreNotifications`, la liste des runs récemment soldés). Deux
 * rendus recopiés auraient fini par annoncer deux choses différentes de la même
 * fin — c'est la recopie que docs/30 §2.2 a mesurée, et la raison d'être des
 * primitives.
 *
 * ## La forme vient d'une veille et d'un choix de variante, tous deux sur pièces
 *
 * La **veille** (commentaire de #928, 2026-09-20) a rendu cinq partis pris, dont
 * quatre vivent ici :
 *
 * 1. **c'est un ÉVÉNEMENT, pas une bulle** — d'après la timeline d'une Pull
 *    Request GitHub, où un commentaire porte un cadre et un avatar, là où un
 *    événement n'est qu'une ligne à icône dans le même flux. Un message écrit
 *    par personne ne prend pas la forme d'un propos tenu par quelqu'un. D'où :
 *    aucun avatar, aucune signature d'auteur — et, jusqu'à #1225, aucune
 *    enveloppe (voir ci-dessous : l'enveloppe est revenue, la bulle non) ;
 * 2. **le livrable a une place FIXE, et « aucun » s'y écrit** — d'après la page
 *    d'un run terminé de GitHub Actions, dont la colonne `Artifacts` affiche
 *    `–` à la place qu'elle occuperait. La ligne « Livrable : … » est donc
 *    **toujours** rendue ; quand il n'y a pas de chemin, elle en donne la
 *    raison (`raisonSansLivrable`) ;
 * 3. **le geste est sur la ligne de ce qu'il vise** — d'après GitHub (« View
 *    details » sur la ligne du merge) et GitLab CI (« Download »/« Browse » à
 *    droite du job). Les deux gestes sont donc **sur la ligne du chemin**, pas
 *    rejetés dessous ;
 * 4. **deux gestes, jamais un seul : aller voir, et emporter** — d'après GitLab
 *    CI. « Ouvrir le dossier » quand la fenêtre le permet, « Copier le chemin »
 *    **toujours** (voir `lib/poste` : le second n'est pas un pis-aller) ;
 * 5. **le geste nomme sa destination** — d'après VS Code (« Reveal in File
 *    Explorer », « Open Containing Folder ») : « Ouvrir le dossier », jamais
 *    « Ouvrir ».
 *
 * Le **choix de variante** (commentaire de #928) a retenu la forme A — une
 * ligne à la fin du fil — contre deux autres. Sa forme n'a pas bougé ; sa place
 * est devenue **l'heure de la fin** (#1290), qui revient au pied du fil tant
 * qu'il n'a ouvert qu'un run, mais plus au second. Le regard neuf qui l'a rendu
 * a relevé sur sa propre capture cinq corrections, toutes appliquées ici :
 * l'annonce **porte son heure** (celle de la fin, pas celle du lancement — le
 * défaut qui a écarté la variante B), son **icône dit l'issue** (deux glyphes
 * distincts, doublés du mot : jamais la couleur seule), les gestes sont
 * **remontés** sur la ligne du chemin, ils ont la **même affordance**, et
 * « Voir le run » est **dans** l'annonce.
 *
 * ## Ce qui ne doit pas se défaire
 *
 * - **rien n'est dérivé d'un événement temps réel** : tout vient de
 *   `lib/issueRun`, donc du persisté (voir son en-tête) — c'est le troisième
 *   critère du ticket, et il se perdrait en une ligne ;
 * - **l'état ne tient jamais à la couleur seule** : `IconeStatutTerminee` /
 *   `IconeStatutEchec` sont deux formes, et le libellé dit la même chose en
 *   toutes lettres (filet `a11y.test.tsx`, docs/30 §3.2) ;
 * - **le chemin reste sélectionnable** : c'est du texte, pas un lien. Un
 *   `file://` serait refusé par la coque (qui ne navigue que sur l'origine
 *   locale) comme par le navigateur (qui bloque `file:` depuis une page http) —
 *   voir le refus consigné dans la veille.
 */

import { useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import {
  IconeCopier,
  IconeDossier,
  IconeStatutEchec,
  IconeStatutTerminee,
} from "@/components/Icones";
import { Bouton, CIBLE_MINIMALE, LienRenvoi } from "@/components/Primitives";
import { MontantDuRun } from "@/components/runs/EtatRun";
import { nomDuRun } from "@/lib/execution";
import { formatDateHeure, formatHeureCourte } from "@/lib/format";
import {
  libelleIssue,
  raisonSansLivrable,
  type IssueRun,
} from "@/lib/issueRun";
import { copierTexte, ouvrirDossier, peutOuvrirDossier } from "@/lib/poste";
import { hrefRun } from "@/lib/navigation";

/**
 * L'annonce complète — celle du fil.
 *
 * `compacte` la resserre pour la cloche : mêmes faits, même chemin, mêmes
 * gestes, mais sans le renvoi vers le run (le panneau se referme sur un clic,
 * et il porte déjà ses propres chemins) et sur le pas typographique du panneau.
 *
 * ## Ce que #1225 y change : dans le fil, elle porte l'enveloppe des gestes
 *
 * Parti pris 5 de la veille de #1225 : *tout ce qui n'est pas de la prose porte
 * la même enveloppe*, d'après Perplexity (la carte « Sources » est la seule
 * enveloppe de la page) et Duck.ai. Les cinq autres objets du fil — proposition
 * de run, équipe, question d'agent, question d'outillage, sa conclusion — sont
 * des `chat/CarteDuFil` ; la fin d'un run était le seul `div` nu, si bien que
 * l'événement le plus important du fil était aussi le moins visible.
 *
 * Elle prend donc **l'enveloppe**, pas la bulle : `ton="creuse"` et non
 * `attention`, parce qu'elle ne demande rien — elle raconte. Le parti pris 1 de
 * la veille de #928 (« un événement, pas une bulle ») tient : ni avatar, ni
 * signature d'auteur, ni côté dans le fil.
 *
 * ⚠ **La cloche garde le rendu nu** (`compacte`). Le panneau de notifications
 * n'est pas le fil : une carte par fin y empilerait des cadres dans 20 rem, et
 * cette liste-là n'a jamais eu à s'accorder avec les gestes d'une conversation.
 */
export function AnnonceIssueRun({
  issue,
  compacte = false,
}: {
  issue: IssueRun;
  compacte?: boolean;
}) {
  const Icone = issue.abouti ? IconeStatutTerminee : IconeStatutEchec;
  const ton = issue.abouti ? "text-positif-texte" : "text-alerte-texte";
  const fin = issue.execution.fin ?? "";
  const run = hrefRun(issue.execution.run_id);
  // Les faits, séparés par le **point médian** que le fil emploie partout
  // ailleurs (« vous · 07:49 », « Agent devops · DevOps ») : juxtaposés par un
  // simple blanc, ils se lisaient comme une suite de mots plutôt que comme
  // trois faits distincts (relevé de la relecture visuelle). Les séparateurs
  // sont `aria-hidden` — un lecteur d'écran marque déjà la frontière entre deux
  // éléments.
  const faits = (
    <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-micro text-texte-secondaire">
      {/* L'heure de la FIN, et c'est ce qui a écarté la variante B : rangée
          sous la bulle qui a lancé le run, l'annonce y portait l'heure du
          lancement. Un run de 53 minutes annonçait sa fin à l'heure où il
          avait commencé. */}
      {fin !== "" && (
        <>
          <time dateTime={fin} title={formatDateHeure(fin)}>
            {formatHeureCourte(fin)}
          </time>
          <Separateur />
        </>
      )}
      <span>
        {issue.execution.nb_taches === 1
          ? "1 tâche"
          : `${issue.execution.nb_taches} tâches`}
      </span>
      <Separateur />
      {/* Le coût du run, et « coût partiel » s'il n'est qu'un plancher (#1280) :
          une fin de run annoncée avec un montant complet qui ne l'est pas
          était précisément le constat du ticket. */}
      <span className="chiffre">
        <MontantDuRun run={issue.execution} />
      </span>
      {!compacte && run !== undefined && (
        <>
          <Separateur />
          <LienRenvoi renvoi={{ href: run, libelle: "Voir le run" }} />
        </>
      )}
    </p>
  );

  if (!compacte) {
    return (
      <CarteDuFil
        ton="creuse"
        libelle={`Fin du run ${nomDuRun(issue.execution)}`}
        icone={Icone}
        /* Le verdict garde sa couleur **dans** le titre : `EnTeteSection` teinte
           ses titres selon son `ton`, et un `creuse` les rend gris. L'état ne
           tient pas pour autant à la couleur — il est écrit, et l'icône en est
           la seconde forme (docs/30 §3.2). */
        titre={<span className={ton}>{libelleIssue(issue)}</span>}
      >
        {/* Le titre du run (#991) : cette annonce est une ligne du fil, et une
            ligne ne porte pas un brief. */}
        <p className="text-corps text-texte">{nomDuRun(issue.execution)}</p>
        <div className="mt-1 flex flex-col gap-1">
          {faits}
          <LivrableDuRun issue={issue} />
        </div>
      </CarteDuFil>
    );
  }

  return (
    <div className="flex min-w-0 gap-2">
      {/* Décorative : le libellé à côté dit l'issue en toutes lettres. */}
      <Icone className={`mt-0.5 size-4 shrink-0 ${ton}`} aria-hidden="true" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-annexe text-texte">
          <span className={`font-medium ${ton}`}>{libelleIssue(issue)}</span>
          {" — "}
          {nomDuRun(issue.execution)}
        </p>
        {faits}
        <LivrableDuRun issue={issue} />
      </div>
    </div>
  );
}

/** Le point médian qui sépare deux faits — décoratif, jamais lu deux fois. */
function Separateur() {
  return <span aria-hidden="true">·</span>;
}

/**
 * Le chemin du livrable et les deux gestes — **toujours rendu**, même sans
 * chemin (parti pris 2 : « aucun » s'écrit à la place qu'il occuperait).
 *
 * Les gestes sont sur la **même ligne** que le chemin (parti pris 3), qui passe
 * à la ligne sous eux quand la largeur ne suffit pas : c'est `flex-wrap`, et
 * c'est ce qui tient dans la colonne de conversation (320 px) comme dans le
 * panneau de la cloche (20 rem) sans seconde mise en page.
 *
 * ⚠ Le `pe-2` du chemin n'est pas un réglage d'esthète : `break-all` remplit la
 * ligne **exactement**, si bien que le `gap` du flex ne se voyait pas et que la
 * fin du chemin venait buter contre la bordure du premier bouton (✗ relevé par
 * la relecture visuelle, `/chat` dans les deux thèmes). On ne savait alors pas,
 * d'un coup d'œil, si le chemin était complet ou coupé — ce qui est exactement
 * ce qu'un chemin doit dire. Le retirer ramène le défaut.
 */
function LivrableDuRun({ issue }: { issue: IssueRun }) {
  const raison = raisonSansLivrable(issue);
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-micro text-texte-secondaire">
      <span className="shrink-0">Livrable :</span>
      {issue.racine !== null ? (
        <>
          <span className="min-w-0 pe-2 font-mono break-all text-texte">
            {issue.racine}
          </span>
          <GestesDuLivrable chemin={issue.racine} />
        </>
      ) : (
        <span>{raison}</span>
      )}
    </p>
  );
}

/**
 * Les deux gestes du livrable : **y aller**, et **l'emporter**.
 *
 * « Ouvrir le dossier » n'apparaît que si le poste sait le faire — une
 * **capacité**, jamais un test de plateforme (`lib/poste`, ENF-12). Dans un
 * onglet il n'y est pas, et « Copier le chemin » reste : c'est le repli demandé
 * par les notes techniques du ticket, et le second geste de GitLab CI.
 *
 * Les deux portent la **même affordance** — deux boutons de contour à la même
 * taille —, correction relevée par le regard neuf : un texte nu posé à côté
 * d'un bouton bordé ne se lit pas comme un geste. Et chacun **dit ce qu'il a
 * fait** : un geste sans retour ne se distingue pas d'une page figée.
 */
function GestesDuLivrable({ chemin }: { chemin: string }) {
  const [dit, setDit] = useState<string | null>(null);
  // Lu au rendu et non dans un effet : la capacité est posée par la coque avant
  // le premier script de la page, elle ne change jamais en cours de vie. Un
  // état initialisé à `false` ferait clignoter le bouton au premier rendu.
  const ouvrable = peutOuvrirDossier();

  const surOuverture = async () => {
    setDit((await ouvrirDossier(chemin)) ? null : "Dossier introuvable");
  };
  const surCopie = async () => {
    setDit((await copierTexte(chemin)) ? "Chemin copié" : "Copie refusée");
  };

  return (
    <>
      {ouvrable && (
        <Bouton
          taille="petite"
          variante="contour"
          className={CIBLE_MINIMALE}
          onClick={() => void surOuverture()}
        >
          <IconeDossier className="size-3.5 shrink-0" aria-hidden="true" />
          Ouvrir le dossier
        </Bouton>
      )}
      <Bouton
        taille="petite"
        variante="contour"
        className={CIBLE_MINIMALE}
        onClick={() => void surCopie()}
      >
        <IconeCopier className="size-3.5 shrink-0" aria-hidden="true" />
        Copier le chemin
      </Bouton>
      {/* `role="status"` : le retour d'un geste se dit aussi à qui ne regarde
          pas le bouton. Il remplace le précédent au lieu de s'empiler — deux
          messages de copie côte à côte ne diraient rien de plus. */}
      {dit !== null && (
        <span role="status" className="text-micro text-texte-secondaire">
          {dit}
        </span>
      )}
    </>
  );
}
