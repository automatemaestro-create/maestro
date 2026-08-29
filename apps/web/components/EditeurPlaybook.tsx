"use client";

/**
 * L'éditeur du playbook d'un agent (ticket #77) : le contenu courant en
 * édition libre, publié comme nouvelle version (`PUT /api/playbooks/{agent}`),
 * et l'historique — versions publiées **et** propositions en attente —
 * consultable depuis un sélecteur, avec restauration d'une version antérieure
 * (`POST /api/playbooks/{agent}/restaurer`, EF-24/EF-25).
 *
 * Le dépôt est append-only (#76) : publier comme restaurer créent une version
 * de plus, rien n'est jamais réécrit — la restauration est donc toujours
 * réversible, aucune confirmation n'est demandée. Depuis #260 l'écran le **dit**
 * au lieu de le supposer : c'est ce qui rend une publication sans regret.
 *
 * L'historique porte aussi les **propositions** d'auto-amélioration en attente
 * (#111/#140) : des brouillons suggérés à partir des échecs d'un run, rangés à
 * part des versions humaines, avec leur justification. Ils ne sont jamais
 * chargés par le moteur tant qu'on ne les a pas appliqués au clic (le contenu
 * devient alors la version courante, chargée à chaud #78) ; un rejet les retire
 * sans toucher à la version courante.
 *
 * Ce que #260 y change, et pourquoi — trois relevés de la revue d'usage :
 *
 * - **Le texte d'aide parlait du modèle interne.** « Une publication crée une
 *   nouvelle version ; les moteurs construits ensuite la chargent » est exact et
 *   n'apprend rien à qui regarde son agent. Ce qu'il faut dire est *à partir de
 *   quand* la publication s'applique, et la réponse vient du moteur :
 *   l'exécuteur relit la version courante **à chaque tâche** (#78), donc une
 *   tâche déjà en cours garde la version avec laquelle elle a démarré.
 *
 * - **Le numéro de version manquait dans certains états.** Il est désormais dans
 *   l'en-tête *et* sur le bouton, pendant l'envoi comme avant, et **y compris
 *   quand le playbook est encore celui du code** — qui est la version `v0`, pas
 *   une absence de version. Un bouton « Publier » qui ne dit pas ce qu'il va
 *   créer demande de faire confiance ; celui-ci nomme la version qu'il produit.
 *
 * - **L'historique s'empilait sous l'éditeur.** Il tient maintenant dans un
 *   sélecteur en haut à droite, et choisir une entrée **remplace** la zone
 *   d'édition par sa lecture seule, à la même hauteur. C'est le point : la
 *   consultation ne pousse plus rien vers le bas et l'écran ne change pas de
 *   taille selon le nombre de versions, là où la liste dépliée grandissait à
 *   chaque publication. Les modifications en cours ne sont pas perdues pour
 *   autant — elles vivent dans l'état de l'éditeur, pas dans le DOM affiché, et
 *   reviennent telles quelles avec lui.
 *
 * ## La rédaction assistée (#261)
 *
 * L'éditeur ne se contente plus d'accepter du texte, il aide à l'écrire — sans
 * jamais publier à la place de qui que ce soit. Deux aides, à deux échelles :
 *
 * - les **complétions en cours de frappe** (`lib/completionsPlaybook`), tirées du
 *   lexique du dépôt (`GET /api/playbooks/lexique`) : structures de section et
 *   tournures que les playbooks livrés ont en commun. Déterministes et locales —
 *   aucun appel modèle par frappe —, elles s'acceptent au `Tab` et s'ignorent en
 *   continuant de taper ou par `Échap` ;
 * - l'**assistant** (`POST …/redaction`), une réécriture du brouillon demandée en
 *   un clic, rendue en **différentiel** (`lib/diff`) avant toute application.
 *
 * ⚠ **Rien de tout cela ne publie.** Accepter une complétion ou appliquer une
 * réécriture ne touche que `contenu`, l'état local de la zone d'édition ; la
 * version en vigueur ne bouge que par le bouton « Publier », qui n'a pas changé.
 * C'est le troisième critère de #261, et c'est pour lui que l'assistance ne passe
 * pas par les propositions stockées de #111/#140 : les appliquer *publie* une
 * version (voir `maestro.controltower.auto_amelioration`).
 *
 * Les deux aides vivent **dans l'édition** et nulle part ailleurs : une entrée
 * d'historique est en lecture seule (#260), il n'y a rien à y compléter ni à y
 * réécrire — le sélecteur referme donc la liste de complétions en passant la
 * main à l'aperçu.
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  IconeAssistant,
  IconeHistorique,
  IconePlaybooks,
} from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  CIBLE_MINIMALE,
  CLASSE_CONTROLE,
  EnTeteSection,
  classesCarte,
} from "@/components/Primitives";
import {
  appliquerPropositionPlaybook,
  chargerLexiquePlaybook,
  chargerPlaybook,
  chargerPropositionPlaybook,
  chargerPropositionsPlaybook,
  chargerVersionPlaybook,
  chargerVersionsPlaybook,
  ecrirePlaybook,
  redigerPlaybook,
  rejeterPropositionPlaybook,
  restaurerPlaybook,
} from "@/lib/api";
import {
  accepter,
  completionsPour,
  segmentCourant,
  type Completion,
} from "@/lib/completionsPlaybook";
import { compter, condenser, differencier } from "@/lib/diff";
import { formatDateHeure } from "@/lib/format";
import {
  PLAYBOOK_SOURCE_DEFAUT,
  type LexiquePlaybook,
  type PlaybookDetail,
  type PropositionPlaybook,
  type RedactionPlaybook,
  type VersionPlaybook,
} from "@/lib/types";

/* ------------------------------------------------------------------ *
 * Ce que le sélecteur désigne
 * ------------------------------------------------------------------ */

/** L'éditeur lui-même : le texte en vigueur, modifiable. */
const CLE_COURANTE = "courante";

const cleVersion = (numero: number) => `v:${numero}`;
const cleProposition = (numero: number) => `p:${numero}`;

type Entree = { genre: "version" | "proposition"; numero: number };

/**
 * La clé d'une option relue. Les deux familles sont numérotées **séparément**
 * (`v3` et `p3` coexistent, #111) : le préfixe n'est donc pas décoratif, c'est
 * lui qui dit à quelle API la demander.
 */
function decoderCle(cle: string): Entree | null {
  const separateur = cle.indexOf(":");
  if (separateur < 0) return null;
  const numero = Number(cle.slice(separateur + 1));
  if (!Number.isFinite(numero)) return null;
  const prefixe = cle.slice(0, separateur);
  if (prefixe === "v") return { genre: "version", numero };
  if (prefixe === "p") return { genre: "proposition", numero };
  return null;
}

/** L'apparence du sélecteur d'historique — le socle, en compact. */
const CLASSE_SELECTEUR =
  `${CIBLE_MINIMALE} max-w-64 rounded-md border border-bord bg-surface px-2 py-1 ` +
  "text-annexe text-texte shadow-sm focus:border-bord-fort " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent " +
  "disabled:opacity-50 [&>option]:bg-surface [&>optgroup]:bg-surface";

/** La zone de texte et son aperçu partagent la même hauteur : l'écran ne saute pas. */
const HAUTEUR_ZONE = "h-96";

export function EditeurPlaybook({
  agent,
  onPublication,
}: {
  agent: string;
  /**
   * Prévenir la page qu'une version a été publiée. Facultatif depuis #190 :
   * l'onglet Playbook d'une fiche agent n'a pas de liste à rafraîchir, l'éditeur
   * se resynchronisant déjà tout seul.
   */
  onPublication?: () => void | Promise<void>;
}) {
  const [fiche, setFiche] = useState<PlaybookDetail | null>(null);
  const [versions, setVersions] = useState<VersionPlaybook[]>([]);
  const [propositions, setPropositions] = useState<PropositionPlaybook[]>([]);
  const [contenu, setContenu] = useState("");
  const [chargement, setChargement] = useState(true);
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  // Ce que le sélecteur montre : l'éditeur, ou une entrée de l'historique en
  // lecture seule. Son contenu se charge à la demande (les deux listes REST ne
  // portent que les métadonnées), d'où l'état de chargement propre à l'aperçu.
  const [selection, setSelection] = useState(CLE_COURANTE);
  const [apercu, setApercu] = useState<string | null>(null);
  const [chargementApercu, setChargementApercu] = useState(false);
  const [erreurApercu, setErreurApercu] = useState<string | null>(null);
  // La dernière entrée demandée : deux choix rapprochés ne doivent pas laisser
  // la réponse la plus lente s'afficher sous le libellé de l'autre.
  const demande = useRef(CLE_COURANTE);

  // Rédaction assistée (#261). Le lexique est chargé une fois — c'est le
  // vocabulaire du dépôt, pas celui de l'agent, et il ne change qu'avec les
  // documents livrés. Un lexique indisponible n'est pas une panne de l'éditeur :
  // les complétions se taisent, tout le reste fonctionne.
  const [lexique, setLexique] = useState<LexiquePlaybook | null>(null);
  const [completions, setCompletions] = useState<Completion[]>([]);
  const [choisie, setChoisie] = useState(0);
  const zone = useRef<HTMLTextAreaElement | null>(null);
  // Une **ref** et non un état : la position à restaurer est un ordre donné au
  // DOM, pas une donnée dont dépend le rendu — la stocker en état ferait un
  // rendu de plus, puis un second pour la remettre à zéro.
  const caret = useRef<number | null>(null);
  const idListe = useId();

  const [assistant, setAssistant] = useState(false);
  const [consigne, setConsigne] = useState("");
  const [redaction, setRedaction] = useState<RedactionPlaybook | null>(null);
  const [redactionEnCours, setRedactionEnCours] = useState(false);
  const [erreurRedaction, setErreurRedaction] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    const [nouvelleFiche, nouvellesVersions, nouvellesPropositions] =
      await Promise.all([
        chargerPlaybook(agent),
        chargerVersionsPlaybook(agent),
        chargerPropositionsPlaybook(agent),
      ]);
    setFiche(nouvelleFiche);
    setVersions(nouvellesVersions);
    setPropositions(nouvellesPropositions);
    return nouvelleFiche;
  }, [agent]);

  // Chargement différé d'un tick (même mécanique que useControlTower) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    let abandonne = false;
    const tick = setTimeout(() => {
      setChargement(true);
      setErreur(null);
      recharger()
        .then((nouvelleFiche) => {
          if (!abandonne) setContenu(nouvelleFiche.contenu);
        })
        .catch((e) => {
          if (!abandonne) setErreur(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!abandonne) setChargement(false);
        });
    }, 0);
    return () => {
      abandonne = true;
      clearTimeout(tick);
    };
  }, [recharger]);

  // Le lexique de complétion, chargé à part du playbook : il ne dépend d'aucun
  // agent, et son absence ne doit rien empêcher — d'où un `catch` qui se tait
  // plutôt qu'une erreur affichée pour une aide à la frappe.
  useEffect(() => {
    let abandonne = false;
    chargerLexiquePlaybook()
      .then((charge) => {
        if (!abandonne) setLexique(charge);
      })
      .catch(() => {
        /* pas de complétions : l'éditeur reste une zone de texte ordinaire */
      });
    return () => {
      abandonne = true;
    };
  }, []);

  // Repositionnement du curseur après acceptation d'une complétion : le texte
  // vient d'être remplacé par React, la position ne peut être posée qu'ensuite.
  // L'effet suit `contenu` et ne fait rien tant qu'aucune position n'attend.
  useEffect(() => {
    const position = caret.current;
    if (position === null) return;
    caret.current = null;
    zone.current?.focus();
    zone.current?.setSelectionRange(position, position);
  }, [contenu]);

  const fermerCompletions = () => {
    setCompletions([]);
    setChoisie(0);
  };

  /**
   * Les complétions se recalculent **à la frappe seulement**. Les rouvrir sur un
   * déplacement de curseur (flèches, clic) ferait surgir une liste que personne
   * n'a demandée, au milieu d'une relecture : une aide à l'écriture s'invite
   * quand on écrit.
   */
  const saisir = (valeur: string, position: number) => {
    setContenu(valeur);
    const trouvees = completionsPour(lexique, segmentCourant(valeur, position));
    setCompletions(trouvees);
    setChoisie(0);
  };

  const accepterCompletion = (completion: Completion) => {
    const position = zone.current?.selectionStart ?? contenu.length;
    const suite = accepter(contenu, position, completion);
    caret.current = suite.position;
    setContenu(suite.texte);
    fermerCompletions();
  };

  /**
   * Le clavier de la liste de complétions. `Entrée` n'y figure pas, à dessein :
   * dans une zone de texte elle insère un saut de ligne, et la voler à quelqu'un
   * qui rédige coûterait plus que l'aide n'apporte. `Tab` accepte — et seulement
   * tant que la liste est ouverte, `Échap` la refermant pour rendre la touche à
   * la navigation.
   */
  const clavier = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (completions.length === 0) return;
    if (e.key === "Tab") {
      e.preventDefault();
      accepterCompletion(completions[choisie]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setChoisie((i) => (i + 1) % completions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setChoisie((i) => (i - 1 + completions.length) % completions.length);
    } else if (e.key === "Escape") {
      e.preventDefault();
      fermerCompletions();
    }
  };

  /**
   * Demande une réécriture du brouillon. **N'écrit rien** : la réponse est un
   * candidat rendu en différentiel, que l'utilisateur applique à son brouillon
   * ou jette. Un échec laisse le texte exactement où il est.
   */
  const demanderRedaction = async () => {
    setRedactionEnCours(true);
    setErreurRedaction(null);
    try {
      setRedaction(await redigerPlaybook(agent, contenu, consigne.trim()));
    } catch (e) {
      setErreurRedaction(e instanceof Error ? e.message : String(e));
    } finally {
      setRedactionEnCours(false);
    }
  };

  /** Applique la réécriture **au brouillon**. La version en vigueur ne bouge pas. */
  const appliquerRedaction = () => {
    if (redaction === null) return;
    setContenu(redaction.contenu);
    setRedaction(null);
    fermerCompletions();
  };

  const fermerAssistant = () => {
    setAssistant(false);
    setRedaction(null);
    setErreurRedaction(null);
  };

  /** Refermer l'aperçu : on revient au texte en vigueur, modifications comprises. */
  const revenirALEdition = useCallback(() => {
    demande.current = CLE_COURANTE;
    setSelection(CLE_COURANTE);
    setApercu(null);
    setErreurApercu(null);
    setChargementApercu(false);
  }, []);

  const choisir = async (cle: string) => {
    demande.current = cle;
    if (cle === CLE_COURANTE) {
      revenirALEdition();
      return;
    }
    const entree = decoderCle(cle);
    if (entree === null) return;
    // La zone d'édition cède la place à l'aperçu : une liste de complétions
    // restée ouverte reviendrait avec elle, sans que personne ait retapé.
    fermerCompletions();
    setSelection(cle);
    setApercu(null);
    setErreurApercu(null);
    setChargementApercu(true);
    try {
      const detail =
        entree.genre === "version"
          ? await chargerVersionPlaybook(agent, entree.numero)
          : await chargerPropositionPlaybook(agent, entree.numero);
      if (demande.current === cle) setApercu(detail.contenu);
    } catch (e) {
      if (demande.current === cle) {
        setErreurApercu(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (demande.current === cle) setChargementApercu(false);
    }
  };

  // Publication, restauration et application d'une proposition partagent la même
  // mécanique : l'action, puis rechargement (fiche + historique + propositions)
  // et resynchronisation de l'éditeur sur la nouvelle version courante.
  // `resynchroniser: false` pour le rejet, qui ne change pas la version courante :
  // l'éditeur garde alors les modifications en cours de l'utilisateur.
  //
  // Toutes referment l'aperçu — l'entrée consultée vient d'être appliquée,
  // restaurée ou écartée : la laisser à l'écran montrerait un état révolu.
  const executer = async (
    action: () => Promise<void>,
    { resynchroniser = true }: { resynchroniser?: boolean } = {},
  ) => {
    setEnCours(true);
    setErreur(null);
    try {
      await action();
      const nouvelleFiche = await recharger();
      if (resynchroniser) setContenu(nouvelleFiche.contenu);
      revenirALEdition();
      await onPublication?.();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  if (chargement) {
    return <p className="text-corps text-texte-secondaire">Chargement du playbook…</p>;
  }
  if (fiche === null) {
    return (
      <p className="text-corps text-alerte-texte" role="alert">
        Playbook illisible : {erreur}
      </p>
    );
  }

  const modifie = contenu !== fiche.contenu;
  const jamaisEdite = fiche.source === PLAYBOOK_SOURCE_DEFAUT;
  const prochaine = fiche.version + 1;

  // Le plus récent d'abord, dans les deux familles.
  const propositionsRecentes = [...propositions].reverse();
  // La version courante n'entre pas dans l'historique consultable : son contenu
  // *est* celui de l'éditeur, et l'offrir deux fois ferait chercher la
  // différence entre les deux.
  const versionsPassees = [...versions]
    .reverse()
    .filter((v) => v.version !== fiche.version);
  const historiqueVide =
    versionsPassees.length === 0 && propositionsRecentes.length === 0;

  const entree = decoderCle(selection);
  const versionConsultee =
    entree?.genre === "version"
      ? (versionsPassees.find((v) => v.version === entree.numero) ?? null)
      : null;
  const propositionConsultee =
    entree?.genre === "proposition"
      ? (propositionsRecentes.find((p) => p.version === entree.numero) ?? null)
      : null;
  const enConsultation = versionConsultee !== null || propositionConsultee !== null;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <section aria-label={`Playbook de ${agent}`}>
        {/* Le nom de l'agent est porté par l'en-tête de la fiche (#190). */}
        <EnTeteSection
          niveau={3}
          icone={IconePlaybooks}
          titre={`Playbook${fiche.role ? ` · ${fiche.role}` : ""}`}
          className="mb-2"
          aside={
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {/* Critère 2 : la version en vigueur se lit sans rien ouvrir, et
                  « playbook du code » en est une — la v0, pas un trou. */}
              <span className="chiffre text-annexe text-texte-secondaire">
                en vigueur : v{fiche.version}
                {jamaisEdite
                  ? " · playbook du code"
                  : fiche.cree_le
                    ? ` · ${formatDateHeure(fiche.cree_le)}`
                    : ""}
              </span>
              {propositionsRecentes.length > 0 && (
                <BadgeEtat ton="accent" className="chiffre">
                  {propositionsRecentes.length} en attente
                </BadgeEtat>
              )}
              {historiqueVide ? (
                <span className="text-annexe text-texte-secondaire">
                  Aucune version antérieure
                </span>
              ) : (
                // Le libellé **entoure** le sélecteur plutôt que de le viser par
                // `htmlFor` : deux fiches agent montées ensemble résoudraient le
                // même identifiant (voir `CadreChamp` dans les primitives).
                <label className="flex items-center gap-1.5 text-annexe text-texte-secondaire">
                  <IconeHistorique className="size-4 shrink-0" />
                  Historique
                  <select
                    value={selection}
                    disabled={enCours}
                    onChange={(e) => void choisir(e.target.value)}
                    className={CLASSE_SELECTEUR}
                  >
                    <option value={CLE_COURANTE}>
                      Version en vigueur · v{fiche.version}
                    </option>
                    {/* Les propositions d'abord : elles attendent une décision. */}
                    {propositionsRecentes.length > 0 && (
                      <optgroup label="Propositions en attente">
                        {propositionsRecentes.map((proposition) => (
                          <option
                            key={cleProposition(proposition.version)}
                            value={cleProposition(proposition.version)}
                          >
                            p{proposition.version} · {proposition.provenance} ·{" "}
                            {formatDateHeure(proposition.cree_le)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {versionsPassees.length > 0 && (
                      <optgroup label="Versions publiées">
                        {versionsPassees.map((version) => (
                          <option
                            key={cleVersion(version.version)}
                            value={cleVersion(version.version)}
                          >
                            v{version.version} · {formatDateHeure(version.cree_le)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                </label>
              )}
            </div>
          }
        />

        {enConsultation ? (
          <ApercuHistorique
            titre={
              versionConsultee
                ? `Version ${versionConsultee.version}`
                : `Proposition ${propositionConsultee?.version}`
            }
            reference={
              versionConsultee
                ? `v${versionConsultee.version}`
                : `p${propositionConsultee?.version}`
            }
            provenance={propositionConsultee?.provenance}
            creeLe={(versionConsultee ?? propositionConsultee)?.cree_le ?? ""}
            justification={propositionConsultee?.justification}
            contenu={apercu}
            chargement={chargementApercu}
            erreur={erreurApercu}
            editionModifiee={modifie}
            enCours={enCours}
            explication={
              versionConsultee
                ? `Restaurer republie ce texte comme version ${prochaine} : la version ${fiche.version} reste dans l’historique, rien n’est écrasé.`
                : `Appliquer publie ce texte comme version ${prochaine}. Rejeter l’écarte sans toucher à la version en vigueur.`
            }
            actions={
              versionConsultee ? (
                <Bouton
                  variante="contour"
                  ton="attention"
                  taille="petite"
                  disabled={enCours}
                  onClick={() =>
                    void executer(() =>
                      restaurerPlaybook(agent, versionConsultee.version),
                    )
                  }
                >
                  Restaurer la version {versionConsultee.version}
                </Bouton>
              ) : (
                propositionConsultee && (
                  <>
                    <Bouton
                      taille="petite"
                      disabled={enCours}
                      onClick={() =>
                        void executer(() =>
                          appliquerPropositionPlaybook(
                            agent,
                            propositionConsultee.version,
                          ),
                        )
                      }
                    >
                      Appliquer
                    </Bouton>
                    <Bouton
                      variante="contour"
                      ton="alerte"
                      taille="petite"
                      disabled={enCours}
                      onClick={() =>
                        void executer(
                          () =>
                            rejeterPropositionPlaybook(
                              agent,
                              propositionConsultee.version,
                            ),
                          { resynchroniser: false },
                        )
                      }
                    >
                      Rejeter
                    </Bouton>
                  </>
                )
              )
            }
            revenir={revenirALEdition}
          />
        ) : (
          <>
            <textarea
              ref={zone}
              value={contenu}
              onChange={(e) => saisir(e.target.value, e.target.selectionStart)}
              onKeyDown={clavier}
              onBlur={fermerCompletions}
              disabled={enCours}
              aria-label="Contenu du playbook"
              spellCheck={false}
              role="combobox"
              aria-expanded={completions.length > 0}
              aria-autocomplete="list"
              {...(completions.length > 0 && {
                "aria-controls": idListe,
                "aria-activedescendant": `${idListe}-${choisie}`,
              })}
              className={`${HAUTEUR_ZONE} w-full resize-y rounded-md border border-bord bg-surface p-3 font-mono text-annexe leading-relaxed text-texte shadow-sm focus:border-bord-fort focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-50`}
            />
            {completions.length > 0 && (
              <ListeCompletions
                id={idListe}
                completions={completions}
                choisie={choisie}
                surChoix={accepterCompletion}
              />
            )}
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {/* Critère 2 : le numéro que le clic va créer, dans tous les états
                  — pendant l'envoi compris, où le libellé changeait de sujet. */}
              <Bouton
                disabled={!modifie || !contenu.trim()}
                occupe={enCours}
                onClick={() => void executer(() => ecrirePlaybook(agent, contenu))}
              >
                {enCours
                  ? `Publication de la version ${prochaine}…`
                  : `Publier la version ${prochaine}`}
              </Bouton>
              {modifie && !enCours && (
                <Bouton
                  variante="contour"
                  ton="neutre"
                  onClick={() => setContenu(fiche.contenu)}
                >
                  Annuler les modifications
                </Bouton>
              )}
              <Bouton
                variante="contour"
                ton="neutre"
                icone={IconeAssistant}
                disabled={enCours}
                aria-expanded={assistant}
                onClick={() => (assistant ? fermerAssistant() : setAssistant(true))}
              >
                Assistant
              </Bouton>
            </div>
            {assistant && (
              <PanneauAssistant
                consigne={consigne}
                setConsigne={setConsigne}
                brouillonVide={!contenu.trim()}
                enCours={redactionEnCours}
                redaction={redaction}
                avant={contenu}
                erreur={erreurRedaction}
                demander={() => void demanderRedaction()}
                appliquer={appliquerRedaction}
                fermer={fermerAssistant}
              />
            )}
            {/* Critère 1 : ce que publier fait, et à partir de quand. « À partir
                de quand » vient du moteur — l'exécuteur relit la version
                courante à chaque tâche (#78) —, pas d'une formule prudente. */}
            <p className="mt-2 text-annexe text-texte-secondaire">
              {modifie && (
                <span className="font-medium text-texte">
                  Modifications non publiées.{" "}
                </span>
              )}
              Publier enregistre ce texte comme version {prochaine} : les tâches
              lancées ensuite l’utiliseront, celles déjà en cours gardent la
              version avec laquelle elles ont démarré. Rien n’est écrasé — chaque
              version reste consultable et restaurable depuis l’historique.
            </p>
          </>
        )}

        {/* L'échec d'une action se dit **hors** des deux états : une restauration
            refusée laisse l'aperçu ouvert, et son message serait alors invisible
            s'il vivait dans la seule branche de l'éditeur. */}
        {erreur && (
          <p className="mt-2 text-annexe text-alerte-texte" role="alert">
            {erreur}
          </p>
        )}
      </section>
    </div>
  );
}

/**
 * Une entrée de l'historique en lecture seule, **à la place** de l'éditeur : le
 * repère (`v2`, `p4`), sa date, ce qu'on peut en faire, et le texte à la même
 * hauteur que la zone d'édition. Occuper la place plutôt que s'ajouter dessous
 * est tout ce que le critère 3 demande — c'est la liste dépliée qui mangeait
 * l'écran, pas la consultation elle-même.
 */
function ApercuHistorique({
  titre,
  reference,
  provenance,
  creeLe,
  justification,
  contenu,
  chargement,
  erreur,
  editionModifiee,
  enCours,
  explication,
  actions,
  revenir,
}: {
  titre: string;
  reference: string;
  /** L'origine d'une proposition — absente sur une version publiée. */
  provenance?: string;
  creeLe: string;
  justification?: string;
  contenu: string | null;
  chargement: boolean;
  erreur: string | null;
  /** L'éditeur porte des modifications non publiées : le dire rassure sur leur sort. */
  editionModifiee: boolean;
  enCours: boolean;
  explication: string;
  actions: ReactNode;
  revenir: () => void;
}) {
  return (
    <div className="rounded-md border border-bord bg-surface-creuse p-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="chiffre font-mono text-annexe font-medium text-texte">
          {reference}
        </span>
        {provenance && <BadgeEtat ton="accent">{provenance}</BadgeEtat>}
        {creeLe && (
          <span className="text-annexe text-texte-secondaire">
            {formatDateHeure(creeLe)}
          </span>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          {actions}
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            disabled={enCours}
            onClick={revenir}
          >
            Revenir à l’édition
          </Bouton>
        </div>
      </div>
      {justification && (
        <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-annexe text-texte">
          {justification}
        </p>
      )}
      {erreur ? (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {erreur}
        </p>
      ) : chargement || contenu === null ? (
        <p
          className={`mt-2 ${HAUTEUR_ZONE} rounded-md border border-bord bg-surface p-3 text-annexe text-texte-secondaire`}
        >
          Chargement du texte…
        </p>
      ) : (
        // La même zone que l'éditeur, en `readOnly` — et non un `<pre>` qui
        // défile : une zone défilante doit être atteignable au clavier (WCAG 2.2
        // §2.1.1), ce qu'un bloc de texte n'obtient qu'avec un `tabIndex` posé
        // sur un élément non interactif. Un `textarea` l'est nativement, se
        // copie, et donne à la lecture le cadre exact de l'écriture.
        // `readOnly` et surtout pas `disabled` : un champ désactivé sort de la
        // tabulation, donc son texte cesse d'être atteignable.
        <textarea
          value={contenu}
          readOnly
          aria-label={`Contenu de ${titre.toLowerCase()}`}
          spellCheck={false}
          className={`mt-2 ${HAUTEUR_ZONE} w-full resize-y rounded-md border border-bord bg-surface p-3 font-mono text-annexe leading-relaxed text-texte shadow-sm focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent`}
        />
      )}
      <p className="mt-2 text-annexe text-texte-secondaire">
        Lecture seule. {explication}
        {editionModifiee && " Vos modifications non publiées sont conservées."}
      </p>
    </div>
  );
}

/**
 * Les complétions ouvertes sous la zone d'édition (#261).
 *
 * `onMouseDown` plutôt que `onClick` : le `blur` de la zone d'édition referme la
 * liste, et il part **avant** le clic — brancher le choix sur `onClick`
 * donnerait une liste qui se dérobe sous le curseur.
 */
function ListeCompletions({
  id,
  completions,
  choisie,
  surChoix,
}: {
  id: string;
  completions: Completion[];
  choisie: number;
  surChoix: (completion: Completion) => void;
}) {
  return (
    <>
      <ul
        id={id}
        role="listbox"
        aria-label="Complétions proposées"
        className="mt-1 flex flex-col overflow-hidden rounded-md border border-bord bg-surface shadow-sm"
      >
        {completions.map((completion, i) => (
          <li
            key={completion.texte}
            id={`${id}-${i}`}
            role="option"
            aria-selected={i === choisie}
            onMouseDown={(e) => {
              e.preventDefault();
              surChoix(completion);
            }}
            className={
              `flex ${CIBLE_MINIMALE} cursor-pointer items-center gap-2 px-3 py-1 ` +
              "text-annexe " +
              (i === choisie ? "bg-accent/10 text-texte" : "text-texte-secondaire")
            }
          >
            <span className="truncate font-mono">{completion.texte}</span>
            <span className="ml-auto shrink-0 text-texte-secondaire">
              {completion.famille === "structure" ? "structure" : "tournure"} ·{" "}
              {completion.roles} playbooks
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-annexe text-texte-secondaire" role="status">
        {completions.length} proposition{completions.length > 1 ? "s" : ""} —
        Tab pour accepter, Échap pour ignorer.
      </p>
    </>
  );
}

/**
 * L'assistant de rédaction (#261) : une consigne libre, une réécriture, et son
 * **différentiel** avant toute application.
 *
 * Bloc d'arbitrage au sens de la règle des trois places (docs/30 §4) — il
 * n'existe que tant qu'il y a quelque chose à trancher, et disparaît dès qu'on
 * ferme l'assistant. Rendu dans la section « Playbook », jamais comme une
 * `<section>` de plus.
 */
function PanneauAssistant({
  consigne,
  setConsigne,
  brouillonVide,
  enCours,
  redaction,
  avant,
  erreur,
  demander,
  appliquer,
  fermer,
}: {
  consigne: string;
  setConsigne: (valeur: string) => void;
  brouillonVide: boolean;
  enCours: boolean;
  redaction: RedactionPlaybook | null;
  /** Le brouillon courant — la gauche du différentiel. */
  avant: string;
  erreur: string | null;
  demander: () => void;
  appliquer: () => void;
  fermer: () => void;
}) {
  const idConsigne = useId();
  return (
    <div
      className={classesCarte({ ton: "creuse", densite: "compacte", className: "mt-3" })}
      role="group"
      aria-label="Assistant de rédaction"
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <label
            htmlFor={idConsigne}
            className="mb-1 block text-annexe text-texte-secondaire"
          >
            Ce que l&apos;assistant doit faire du brouillon (facultatif)
          </label>
          <input
            id={idConsigne}
            value={consigne}
            onChange={(e) => setConsigne(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || enCours || brouillonVide) return;
              e.preventDefault();
              demander();
            }}
            placeholder="resserre les garde-fous, ajoute une section Méthode…"
            className={CLASSE_CONTROLE}
          />
        </div>
        <Bouton
          icone={IconeAssistant}
          occupe={enCours}
          disabled={brouillonVide}
          onClick={demander}
        >
          {enCours
            ? "Rédaction…"
            : redaction
              ? "Proposer à nouveau"
              : "Proposer une réécriture"}
        </Bouton>
        <Bouton variante="discret" ton="neutre" onClick={fermer}>
          Fermer
        </Bouton>
      </div>
      {brouillonVide && (
        <p className="mt-2 text-annexe text-texte-secondaire">
          Le brouillon est vide : écrivez-en une première ligne, l&apos;assistant
          part de ce que vous avez déjà.
        </p>
      )}
      {erreur && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {erreur} — votre brouillon est intact, vous pouvez réessayer.
        </p>
      )}
      {redaction && (
        <Differentiel
          avant={avant}
          apres={redaction.contenu}
          justification={redaction.justification}
          appliquer={appliquer}
          ignorer={fermer}
        />
      )}
    </div>
  );
}

/**
 * Ce que la réécriture changerait au brouillon, ligne à ligne (#261).
 *
 * Le modèle rend un document **intégral** — c'est le contrat de l'API, partagé
 * avec les propositions d'après-run —, donc la comparaison se fait ici. Les
 * longues plages inchangées sont repliées (`condenser`) : un playbook réécrit
 * garde l'essentiel de son texte, et les montrer toutes noierait les cinq lignes
 * qui changent.
 */
function Differentiel({
  avant,
  apres,
  justification,
  appliquer,
  ignorer,
}: {
  avant: string;
  apres: string;
  justification: string;
  appliquer: () => void;
  ignorer: () => void;
}) {
  const lignes = differencier(avant, apres);
  const { ajouts, retraits } = compter(lignes);
  const entrees = condenser(lignes);
  const inchange = ajouts === 0 && retraits === 0;
  return (
    <div className="mt-3">
      <p className="text-annexe text-texte-secondaire">{justification}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <BadgeEtat ton={inchange ? "neutre" : "positif"} className="chiffre">
          {inchange ? "aucun changement" : `+${ajouts} / −${retraits} lignes`}
        </BadgeEtat>
        <span className="text-annexe text-texte-secondaire">
          Différentiel avec votre brouillon — rien n&apos;est encore appliqué.
        </span>
      </div>
      <div
        role="region"
        aria-label="Différentiel de la réécriture proposée"
        className="mt-2 max-h-80 overflow-auto rounded-md border border-bord bg-surface font-mono text-annexe"
      >
        {entrees.map((entree, i) =>
          entree.type === "repli" ? (
            <p
              key={`r${i}`}
              className="bg-surface-creuse px-2 py-0.5 text-center text-texte-secondaire"
            >
              ⋯ {entree.lignes} lignes inchangées
            </p>
          ) : (
            <p
              key={`l${i}`}
              className={
                "whitespace-pre-wrap px-2 py-0.5 " +
                (entree.type === "ajout"
                  ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"
                  : entree.type === "retrait"
                    ? "bg-rose-50 text-rose-900 dark:bg-rose-950 dark:text-rose-200"
                    : "text-texte-secondaire")
              }
            >
              <span aria-hidden className="mr-2 select-none opacity-60">
                {entree.type === "ajout" ? "+" : entree.type === "retrait" ? "−" : " "}
              </span>
              {entree.texte || " "}
            </p>
          ),
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Bouton disabled={inchange} onClick={appliquer}>
          Appliquer au brouillon
        </Bouton>
        <Bouton variante="contour" ton="neutre" onClick={ignorer}>
          Ignorer
        </Bouton>
        <span className="text-annexe text-texte-secondaire">
          Appliquer ne publie rien : le texte part dans la zone d&apos;édition, et
          la publication reste un geste à part.
        </span>
      </div>
    </div>
  );
}
