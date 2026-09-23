"use client";

/**
 * **La** carte d'une demande de validation humaine (#48) — une présentation,
 * toutes les surfaces (#1228).
 *
 * Elle était écrite deux fois : en plein dans `PanneauValidations` (tableau de
 * bord et page), en resserré dans `CentreNotifications` (la cloche). Les deux
 * ne disaient pas la même chose du même acte — la compacte affichait le
 * **titre de la tâche** là où la pleine met l'**acte** en tête depuis #573,
 * c'est-à-dire « Rédiger le README » au-dessus d'un `rm -rf`. Une seule carte,
 * donc, avec une **densité** pour seule variable : ce qui change est la place
 * qu'on lui donne, jamais ce qu'on lit pour trancher. #1183 la montera dans le
 * fil sans rien recopier non plus.
 *
 * Trois décisions viennent de #272 et ne bougent pas :
 *
 * 1. **L'ordre des blocs est celui de la décision** : ce qu'on approuve (l'acte
 *    ou le titre) et depuis quand ça attend, puis qui le demande, puis ce que ça
 *    ferait (diff, arguments ou description), puis pourquoi c'est sensible, puis
 *    seulement les gestes. Un bouton lu avant sa question est un bouton qu'on
 *    clique sans lire.
 * 2. **Le refus motivé ne coûte rien à qui n'en veut pas** : « Refuser » refuse,
 *    en un clic ; un bouton discret ouvre à côté un motif facultatif, qui part
 *    avec ce même bouton. Rien dans l'écran ne bouge tant que rien n'est tranché.
 * 3. **Le temps réel est tenu par la clé de React** : chaque carte est keyée sur
 *    `tache_id` par ses appelants, donc une demande tranchée ailleurs démonte
 *    *sa* carte et emporte son état local (motif en cours de frappe, erreur,
 *    envoi en vol). Sans cette clé, la file se décalant d'un cran, un motif
 *    écrit pour une demande se retrouverait attaché à la suivante.
 *
 * Ce que #1228 y ajoute est **le panneau** (`PanneauValidation`) et **le geste
 * qui l'ouvre** (`GesteValidation`) : un nœud de pipeline fait 16 rem et une
 * carte de Kanban 11 rem, aucun des deux n'a la place de porter un diff et un
 * champ de motif. Le socle a déjà répondu à cette question une fois — le détail
 * d'une tâche s'ouvre **sur place**, dans un panneau modal, sans navigation
 * (#251, `components/PanneauDetailTache`) —, et c'est cette réponse-là qu'on
 * applique plutôt qu'une deuxième. La veille du ticket l'a retrouvée dehors :
 * GitHub Actions ouvre « Review deployments » en pop-up **sur la page du run**,
 * GitLab ouvre l'approbation depuis le **badge** qui dit que ça attend, et les
 * deux gardent un commentaire facultatif.
 */

import { useEffect, useRef, useState } from "react";

import { IconeAgent, IconeFermer } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  ChampTexte,
  CIBLE_MINIMALE,
  type DensiteCarte,
} from "@/components/Primitives";
import { formatAttente } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { usePiegeDeFocus } from "@/lib/usePiegeDeFocus";
import {
  NATURE_AJOUT,
  NATURE_MODIFICATION,
  NATURE_SUPPRESSION,
  type DiffProjet,
  type Validation,
} from "@/lib/types";

/**
 * Trancher une demande. `motif` accompagne un **refus** et reste facultatif :
 * omis, l'appel est celui d'avant #272 — c'est la signature du contexte global
 * (`lib/useControlTower`), reprise telle quelle plutôt que redéclarée au plus
 * étroit, sans quoi la carte ne pourrait plus proposer de motiver.
 */
export type Decider = (
  tacheId: string,
  approuve: boolean,
  motif?: string,
) => Promise<void>;

/**
 * De quoi trancher **sur place** depuis une lecture dense (#1228) : les demandes
 * qui dorment, par tâche (`lib/validations.arbitragesEnAttente`), et le décideur
 * du contexte.
 *
 * Les deux voyagent ensemble parce qu'aucun ne sert seul : un décideur sans
 * demande n'affiche rien, une demande sans décideur est un bouton qui ment. Et
 * ils voyagent en **props** plutôt que par le contexte, contrairement aux ordres
 * de `GestesRun` : le Kanban et le pipeline sont montés par des écrans qui les
 * testent hors du shell, et un `useEtatGlobal` au fond d'une carte y lèverait.
 */
export type ArbitrageSurPlace = {
  enAttente: ReadonlyMap<string, Validation>;
  decider: Decider;
};

/**
 * Une demande, telle qu'on la lit pour trancher.
 *
 * `densite` n'ajoute ni ne retire **aucun champ** — elle règle l'air autour et
 * le pas du texte : `aeree` pour la tête d'une file, `normale` pour les
 * suivantes et pour un panneau, `compacte` pour la cloche. Ce qui met une
 * demande en avant est sa **place**, jamais un contenu réservé.
 */
export function CarteValidation({
  validation,
  decider,
  maintenant,
  densite = "normale",
}: {
  validation: Validation;
  decider: Decider;
  /** L'instant de l'horloge partagée — passé, pour qu'une file n'en ouvre qu'une. */
  maintenant: number | null;
  densite?: DensiteCarte;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const [motifOuvert, setMotifOuvert] = useState(false);
  const [motif, setMotif] = useState("");
  const compacte = densite === "compacte";

  const surDecision = async (approuve: boolean) => {
    setEnCours(true);
    setErreur(null);
    // Le motif n'accompagne que le refus, et n'est **passé** que s'il y en a
    // un : sans lui l'appel est exactement celui d'avant #272, ce qui garde
    // « approuver » et « refuser sec » hors de portée d'une régression du canal
    // motivé.
    const raison = approuve ? "" : motif.trim();
    try {
      if (raison) await decider(validation.tache_id, false, raison);
      else await decider(validation.tache_id, approuve);
      // Succès : la demande sort de « en attente » au rechargement et la carte
      // se démonte — inutile de rendre la main. On ne la rend qu'en cas d'échec,
      // sans quoi un rechargement lent rouvrirait les boutons sur une décision
      // déjà partie, et le second clic reviendrait en 409.
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  // L'acte a la tête quand il y en a un (#581) ; sinon on retombe sur le titre
  // de la tâche, qui est ce que la demande portait de plus parlant avant #573.
  const acte = validation.outil;
  const attente = formatAttente(validation.horodatage, maintenant);
  const idMotif = `motif-refus-${validation.tache_id}`;
  const taille = compacte ? "petite" : "normale";

  return (
    <Carte
      ton="attentionClaire"
      densite={densite}
      className={`w-full ${compacte ? "text-annexe" : "text-corps"}`}
    >
      {/* La question et son ancienneté sur la même ligne, et le titre seul dans
          son paragraphe : c'est ce qu'on lit en premier, ça ne se partage pas
          avec une pastille. */}
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 font-medium" title={validation.tache_id}>
          {acte ? (
            <>
              <span className="text-texte-secondaire">Appel de </span>
              <span className="font-mono">{acte}</span>
            </>
          ) : (
            validation.titre || validation.tache_id
          )}
        </p>
        {attente && (
          <BadgeEtat ton="attention" className="chiffre shrink-0">
            {attente}
          </BadgeEtat>
        )}
      </div>
      <p className="mt-1 flex flex-wrap items-center gap-1 text-annexe text-texte-secondaire">
        <IconeAgent className="size-3.5 shrink-0" />
        Agent {validation.agent}
        {validation.role ? ` · ${validation.role}` : ""}
        {/* Le titre de la tâche reste lisible, une place plus bas : il dit d'où
            vient l'acte, il ne dit pas ce qu'on approuve. */}
        {acte && validation.titre ? ` · ${validation.titre}` : ""}
      </p>
      {validation.diff ? (
        <DiffApplication diff={validation.diff} />
      ) : acte ? (
        <ArgumentsActe arguments={validation.arguments} />
      ) : (
        validation.description && (
          // Écrêtée en compacte, entière ailleurs : dans 20 rem de cloche, une
          // description de quinze lignes pousse les deux boutons hors du
          // panneau, et une demande dont on ne voit plus la réponse n'en est
          // plus une. Le `title` rend le texte entier à la souris.
          <p
            className={`mt-2 whitespace-pre-wrap text-annexe text-texte-secondaire ${compacte ? "line-clamp-2" : ""}`}
            title={compacte ? validation.description : undefined}
          >
            {validation.description}
          </p>
        )
      )}
      {validation.raison && (
        <p className="mt-2 text-annexe text-attention-texte italic">
          Motif : {validation.raison}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Bouton
          taille={taille}
          className={CIBLE_MINIMALE}
          occupe={enCours}
          onClick={() => void surDecision(true)}
        >
          {enCours ? "Envoi…" : "Approuver"}
        </Bouton>
        <Bouton
          variante="contour"
          ton="alerte"
          taille={taille}
          className={CIBLE_MINIMALE}
          disabled={enCours}
          onClick={() => void surDecision(false)}
        >
          Refuser
        </Bouton>
        {/* Le motif n'est pas une étape du refus : il s'ouvre à côté, et c'est
            toujours « Refuser » qui tranche. Rendre le refus conditionnel à une
            saisie ferait payer à chaque demande le prix de celles qu'on veut
            expliquer.

            Refermer **efface** ce qui a été écrit, et ce n'est pas un détail :
            un motif conservé hors de l'écran partirait quand même avec le refus,
            c'est-à-dire un texte envoyé au journal du run que plus personne
            n'avait sous les yeux. « Sans motif » doit vouloir dire sans motif.

            Il est là **aussi dans la cloche** depuis #1228, où il manquait : le
            canal du motif est celui qui réoriente l'agent (#1185), et un refus
            sec depuis la cloche lui rend la main sans rien lui dire. */}
        <Bouton
          variante="discret"
          ton="alerte"
          taille="petite"
          className={CIBLE_MINIMALE}
          disabled={enCours}
          aria-expanded={motifOuvert}
          // Posé seulement quand la zone existe : `aria-controls` visant un
          // identifiant absent est une référence morte, et axe la tolère au
          // repli sans qu'on ait à s'en remettre à cette tolérance.
          aria-controls={motifOuvert ? idMotif : undefined}
          onClick={() => {
            setMotifOuvert((avant) => !avant);
            if (motifOuvert) setMotif("");
          }}
        >
          {motifOuvert ? "Sans motif" : "Motiver le refus"}
        </Bouton>
      </div>
      {motifOuvert && (
        <ChampTexte
          id={idMotif}
          className="mt-2"
          libelle="Motif du refus (facultatif)"
          aide="Il part avec le refus, dans le journal du run — l'approbation l'ignore."
          rows={2}
          maxLength={500}
          disabled={enCours}
          value={motif}
          onChange={(e) => setMotif(e.target.value)}
        />
      )}
      {erreur && (
        <p className="mt-2 text-annexe font-medium text-alerte-texte">{erreur}</p>
      )}
    </Carte>
  );
}

/**
 * Ce qu'on passe à l'outil (#581) : une ligne par argument, la clé puis sa
 * valeur. Le nom de l'outil n'y est pas répété — il est en tête de la carte,
 * c'est lui la question.
 *
 * La valeur est rendue **telle qu'elle a été composée**, sauts de ligne compris
 * (`whitespace-pre-wrap`) : un script passé à `Bash` aplati en une ligne se lit
 * autrement qu'il ne s'exécutera, et approuver ce qu'on lit mal n'est pas
 * approuver. Le backend l'a déjà bornée et expurgée (`maestro.acte`,
 * `evenement_demande`) — il n'y a donc rien à couper ici.
 *
 * Comme le diff, le bloc **défile** au-delà d'une poignée de lignes plutôt que
 * de pousser Approuver/Refuser hors de l'écran : une demande dont on ne voit
 * plus la réponse n'en est plus une.
 */
function ArgumentsActe({ arguments: args }: { arguments: Record<string, string> | null }) {
  const entrees = Object.entries(args ?? {});
  if (entrees.length === 0) {
    // Un outil sans paramètre existe, et un producteur qui n'en rapporte aucun
    // aussi : les deux se disent du même mot, aucun n'est une anomalie à signaler.
    return (
      <p className="mt-2 text-annexe text-texte-secondaire italic">
        Aucun argument
      </p>
    );
  }
  return (
    <dl className="mt-2 max-h-48 overflow-y-auto rounded-controle border border-bord bg-surface-creuse px-2 py-1.5 font-mono text-annexe">
      {entrees.map(([cle, valeur]) => (
        <div key={cle} className="flex items-baseline gap-2 py-0.5">
          <dt className="shrink-0 text-texte-secondaire">{cle}</dt>
          <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-texte">
            {valeur}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Le diff d'une demande d'application dans le projet (#227, EF-37) : l'en-tête
 * dit ce qui se produirait à l'accord — fusion d'une branche pour un projet
 * versionné, écriture des fichiers sinon — puis chaque fichier avec ses lignes.
 *
 * Le tableau **défile** au-delà d'une quinzaine de fichiers plutôt que de
 * pousser les boutons Approuver/Refuser hors de l'écran : une demande de
 * validation dont on ne voit plus la réponse n'en est plus une.
 */
function DiffApplication({ diff }: { diff: DiffProjet }) {
  return (
    <div className="chiffre mt-2 rounded-controle border border-bord bg-surface-creuse text-annexe">
      <p className="border-b border-bord px-2 py-1.5 text-texte">
        <span className="font-medium">
          {diff.fichiers} fichier{diff.fichiers > 1 ? "s" : ""}
        </span>{" "}
        <span className="text-emerald-600 dark:text-emerald-400">+{diff.ajouts}</span>{" "}
        <span className="text-rose-600 dark:text-rose-400">−{diff.suppressions}</span>
        <br />
        <span className="text-texte-secondaire">
          {diff.branche
            ? `Fusion de ${diff.branche} vers ${diff.base}`
            : "Écriture des fichiers dans le projet (non versionné)"}
        </span>
      </p>
      <ul className="max-h-48 overflow-y-auto px-2 py-1.5 font-mono">
        {diff.modifications.map((modification) => (
          <li key={modification.chemin} className="flex items-baseline gap-2 py-0.5">
            <span
              aria-hidden
              className={`w-3 shrink-0 text-center ${couleurNature(modification.nature)}`}
            >
              {SIGNE_NATURE[modification.nature] ?? "~"}
            </span>
            <span className="min-w-0 flex-1 break-all text-texte">
              {modification.chemin}
            </span>
            <span className="shrink-0 text-texte-secondaire">
              {modification.binaire
                ? "binaire"
                : `+${modification.ajouts} −${modification.suppressions}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SIGNE_NATURE: Record<string, string> = {
  [NATURE_AJOUT]: "+",
  [NATURE_MODIFICATION]: "~",
  [NATURE_SUPPRESSION]: "−",
};

function couleurNature(nature: string): string {
  if (nature === NATURE_AJOUT) return "text-emerald-600 dark:text-emerald-400";
  if (nature === NATURE_SUPPRESSION) return "text-rose-600 dark:text-rose-400";
  return "text-amber-600 dark:text-amber-400";
}

/**
 * La même carte, **ouverte par-dessus une lecture dense** (#1228) — la seule
 * façon de trancher depuis un nœud de 16 rem ou une carte de 11 rem sans rien
 * quitter.
 *
 * C'est la coque de `PanneauDetailTache` (#251), reprise au trait près :
 * tiroir à droite, voile, `aria-modal`, Échap qui ferme, focus rendu au
 * déclencheur par l'appelant — lui seul sait d'où l'on est parti. Une seconde
 * forme de modale aurait dit au lecteur qu'il se passe autre chose ; il ne se
 * passe pas autre chose.
 *
 * Il se ferme **de lui-même** quand la demande est tranchée : l'appelant ne
 * trouve plus la validation dans la file rafraîchie, et le panneau se démonte.
 * C'est ce qui fait que « la tâche qui attendait repart sous les yeux » sans
 * qu'aucun code ne l'orchestre.
 */
export function PanneauValidation({
  validation,
  decider,
  fermer,
}: {
  validation: Validation;
  decider: Decider;
  fermer: () => void;
}) {
  const panneau = useRef<HTMLDivElement>(null);
  const maintenant = useHorloge();
  const nom = validation.outil || validation.titre || validation.tache_id;

  // Échap ferme. Le focus revient au déclencheur : c'est l'appelant qui le rend.
  useEffect(() => {
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key === "Escape") fermer();
    };
    document.addEventListener("keydown", surTouche);
    return () => document.removeEventListener("keydown", surTouche);
  }, [fermer]);

  // Le panneau prend le focus à l'ouverture, sans quoi Échap ne serait entendu
  // que par le document et la lecture d'écran resterait sur la carte.
  useEffect(() => panneau.current?.focus(), []);

  // La tabulation reste dans le panneau : il se déclare `aria-modal`, il doit
  // donc l'être pour le clavier aussi (#536).
  usePiegeDeFocus(panneau);

  return (
    <>
      {/* Le voile **arrête le clic** en plus de fermer, contrairement à celui du
          détail d'une tâche : ce panneau-ci est monté *dans* la carte qui l'a
          ouvert (un nœud, une carte de Kanban), et ces cartes ouvrent leur
          propre détail au clic. Sans cet arrêt, refermer la demande ouvrirait le
          panneau de la tâche par-dessus. Le clic **dans** le dialogue, lui, est
          écarté par ces cartes elles-mêmes, qui ignorent ce qui vient d'un
          `[role="dialog"]`. */}
      <div
        aria-hidden="true"
        onClick={(evenement) => {
          evenement.stopPropagation();
          fermer();
        }}
        className="fixed inset-0 z-40 bg-slate-950/50"
      />
      <div
        ref={panneau}
        role="dialog"
        aria-modal="true"
        aria-label={`Trancher la demande ${nom}`}
        tabIndex={-1}
        className={
          "fixed inset-y-0 right-0 z-50 flex w-[min(28rem,100vw)] flex-col overflow-hidden " +
          // `shadow-flottant` — LE pas d'ombre du barème — et non le
          // `shadow-2xl` que le panneau de détail d'une tâche écrit encore : une
          // surface flottante se sépare par une ombre choisie une fois
          // (`tests/rayons-ombres.test.ts`), et les deux panneaux ne sont jamais
          // à l'écran ensemble.
          "border-l border-bord bg-surface text-left shadow-flottant outline-none"
        }
      >
        <header className="flex items-start gap-2 border-b border-bord px-4 py-3">
          <h2 className="min-w-0 flex-1 text-corps font-semibold text-texte">
            Validation en attente
          </h2>
          {/* Le bouton du socle, et non le `<button>` à `p-1.5` que porte
              encore le panneau de détail d'une tâche : la densité se choisit une
              fois (`tests/espacements.test.ts`), et un écran neuf s'y replie
              plutôt que d'allonger un résidu qui ne peut que décroître. Le nom
              accessible passe par un libellé masqué — l'icône est décorative,
              comme partout dans le jeu. */}
          <Bouton
            variante="discret"
            ton="neutre"
            taille="petite"
            icone={IconeFermer}
            onClick={fermer}
          >
            <span className="sr-only">Fermer la demande de validation</span>
          </Bouton>
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <CarteValidation
            validation={validation}
            decider={decider}
            maintenant={maintenant}
          />
        </div>
      </div>
    </>
  );
}

/**
 * Le geste qu'un nœud de pipeline ou une carte de Kanban porte quand une
 * demande dort sur sa tâche (#1228) : **un bouton**, et le panneau qu'il ouvre.
 *
 * Un bouton et non le lien « Trancher → » d'avant : la différence n'est pas
 * cosmétique, c'est tout le ticket. Le lien emmenait sur `/validations` sans
 * chemin de retour — « je suis dans la vue pipeline, je dois trancher, ça me
 * redirige et je ne sais pas comment revenir » (retour d'usage du 2026-09-22).
 *
 * Ne rend rien sans demande : une carte au repos est exactement la carte d'avant
 * ce ticket.
 */
export function GesteValidation({
  tacheId,
  arbitrer,
  className = "",
}: {
  tacheId: string;
  arbitrer?: ArbitrageSurPlace;
  className?: string;
}) {
  const [ouvert, setOuvert] = useState(false);
  const declencheur = useRef<HTMLButtonElement | null>(null);
  const validation = arbitrer?.enAttente.get(tacheId);

  // Refermé **par la donnée** : la demande tranchée quitte la file, `validation`
  // devient indéfini et le panneau se démonte avec elle. Aucun `setOuvert(false)`
  // à la décision, donc aucune fenêtre où le panneau survivrait à son objet.
  if (validation === undefined || arbitrer === undefined) return null;

  const fermer = () => {
    setOuvert(false);
    declencheur.current?.focus();
  };

  return (
    <>
      <Bouton
        variante="contour"
        ton="attention"
        taille="petite"
        className={`${CIBLE_MINIMALE} ${className}`.trim()}
        aria-haspopup="dialog"
        aria-expanded={ouvert}
        onClick={(evenement) => {
          declencheur.current = evenement.currentTarget;
          setOuvert(true);
        }}
      >
        Trancher
      </Bouton>
      {ouvert && (
        <PanneauValidation
          validation={validation}
          decider={arbitrer.decider}
          fermer={fermer}
        />
      )}
    </>
  );
}
