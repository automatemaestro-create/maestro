"use client";

/**
 * Le centre de notifications déroulant de la barre supérieure (#119, lot 3 de
 * #116). Une cloche, présente sur toutes les pages via le shell, signale d'un
 * badge le nombre de validations humaines (#48) en attente et ouvre un panneau
 * qui les liste — chacune approuvable / refusable sur place, sans quitter la
 * page courante — puis rappelle l'activité récente notable.
 *
 * Le badge suit les `validations` du contexte global (rechargées à chaque
 * événement WebSocket) : il se met à jour en temps réel. Une demande **traitée**
 * (approuvée ou refusée) quitte l'état « en attente » et disparaît donc du badge
 * de lui-même ; le panneau, lui, reste consultable pour les événements récents
 * même quand plus rien n'attend d'arbitrage.
 *
 * Le comportement du menu (clic à l'extérieur, Échap, focus rendu au bouton)
 * reprend celui de la bascule de thème (#118, `BasculeTheme`).
 *
 * Validations et événements viennent du contexte, donc **du projet actif**
 * (#281) : la cloche ne réclame jamais un arbitrage qui appartient à un projet
 * qu'on n'a pas sous les yeux, et le badge ne compte pas ce qu'on ne peut pas
 * trancher depuis cet écran.
 *
 * Depuis #322 le badge compte **deux** familles d'attente : les validations
 * humaines (#48) et les **briefs** sur lesquels un run s'est arrêté (#320, #321).
 * Une seule pastille pour les deux, et c'est le point : ce qu'elle répond n'est
 * pas « combien de validations » mais « combien de choses m'attendent » — deux
 * compteurs côte à côte obligeraient à faire la somme soi-même, et un brief
 * suspendu resterait invisible tant que la file de validations n'est pas vide.
 * Les briefs y sont **acheminés, pas tranchés** : la carte compacte mène à
 * l'écran, là où une validation se décide sur place — sept sections, des
 * questions et un coût ne tiennent pas dans un panneau de 20 rem, et approuver
 * sans lire est exactement ce que le point de contrôle empêche.
 *
 * **Trois familles depuis #1025** : les **questions libres** d'un agent (#1023)
 * rejoignent le même compte, pour la raison qui y avait fait entrer les briefs —
 * c'est « combien de choses m'attendent » que la pastille répond. Et elles sont
 * **acheminées, pas tranchées**, comme eux : la veille du ticket l'a refusé en
 * toutes lettres, une question se répond **dans le fil**, là où on a la
 * conversation qui l'a produite. Un second champ de réponse ici en ferait deux,
 * et celui de la cloche répondrait sans le contexte.
 *
 * **La carte de validation n'est plus écrite ici** (#1228) : c'est
 * `components/CarteValidation`, en densité compacte, celle-là même que le
 * tableau de bord, la page, l'en-tête d'un run et ses lectures denses montent.
 * La recopie resserrée qui vivait dans ce fichier disait autre chose du même
 * acte — le **titre de la tâche** au lieu de l'**acte** que #573 met en tête,
 * pas d'arguments, pas de diff, et aucun motif de refus. Une demande ne se lit
 * pas autrement selon la surface d'où on la tranche.
 */

import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import { CarteValidation } from "@/components/CarteValidation";
import {
  IconeAgent,
  IconeBrief,
  IconeFlecheDroite,
  IconeNotifications,
} from "@/components/Icones";
import { LigneActivite } from "@/components/LigneActivite";
import { BadgeEtat, Carte, CIBLE_MINIMALE } from "@/components/Primitives";
import { AnnonceIssueRun } from "@/components/runs/AnnonceIssueRun";
import { resumeArbitrages } from "@/lib/annonces";
import { PAGE_DU_CADRAGE, runsEnAttente } from "@/lib/brief";
import { estNotableNotification, grouperEvenements } from "@/lib/evenements";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { nomDuRun } from "@/lib/execution";
import { useHorloge } from "@/lib/horloge";
import {
  aDesIssuesNonLues,
  ecrireIssuesVues,
  issuesRecentes,
  lireIssuesVues,
} from "@/lib/issueRun";
import { entreeParLibelle } from "@/lib/navigation";
import { PAGE_DES_QUESTIONS, questionsEnAttente } from "@/lib/questions";
import { useSurfaceDeroulee } from "@/lib/useSurfaceDeroulee";
import {
  EXECUTION_EN_ATTENTE_REPONSES,
  VALIDATION_EN_ATTENTE,
  type Question,
  type ResumeExecution,
} from "@/lib/types";

/**
 * Nombre de lignes d'activité récente rappelées dans le panneau — des lignes
 * depuis #250, où une rafale repliée n'en occupe qu'une.
 */
const MAX_EVENEMENTS_NOTABLES = 8;

/**
 * Nombre de **fins de run** rappelées (#928). Moins que les événements ci-dessus
 * parce qu'une fin porte quatre lignes — verdict, faits, chemin, gestes — là où
 * une ligne d'activité en porte une : cinq remplissent déjà la hauteur du
 * panneau, et ce qui déborde se lit sur l'écran Runs.
 */
const MAX_ISSUES_RAPPELEES = 5;

/**
 * Ce que la cloche annonce — le seul endroit où le compte est **nommé**, la
 * pastille n'étant qu'un chiffre décoratif (`aria-hidden`).
 *
 * La formule elle-même vit dans `lib/annonces` depuis #538 : la région assertive
 * du shell dit la **même** file, avec les mêmes mots, et deux formulations
 * auraient fini par diverger — c'est déjà la raison d'être de `lib/brief`. Ce qui
 * reste ici est le cadrage propre à la cloche : le nom du bouton quand rien
 * n'attend.
 */
function etiquetteCloche(
  validations: number,
  briefs: number,
  questions: number,
  issuesNeuves: boolean,
): string {
  const resume = resumeArbitrages(validations, briefs, questions);
  // Les fins de run viennent **après** la file d'arbitrage et ne s'y ajoutent
  // pas : « 2 à valider, et du travail terminé » se lit dans l'ordre où l'on
  // agit. Sans arbitrage en attente, la phrase tient seule.
  const fins = issuesNeuves ? "du travail terminé" : "";
  if (resume === null && fins === "") return "Notifications";
  if (resume === null) return `Notifications — ${fins}`;
  return fins === ""
    ? `Notifications — ${resume}`
    : `Notifications — ${resume}, et ${fins}`;
}

export function CentreNotifications() {
  const { validations, executions, evenements, projet, questions, decider } =
    useEtatGlobal();
  const [ouvert, setOuvert] = useState(false);
  // Le repère de lecture des fins de run (#928), lu **une fois par ouverture**
  // du composant et reposé à chaque ouverture du panneau : le tenir dans un état
  // plutôt que de relire le stockage à chaque rendu est ce qui fait disparaître
  // le point au moment où l'on ouvre, sans attendre le rendu suivant.
  const [issuesVues, setIssuesVues] = useState(() => lireIssuesVues());
  // L'horloge partagée (#1228) : la carte de validation y lit « depuis quand
  // ça attend », et une horloge par carte en ouvrirait une par demande.
  const maintenant = useHorloge();
  const conteneur = useRef<HTMLDivElement>(null);
  const declencheur = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);

  const enAttente = validations.filter(
    (v) => v.statut === VALIDATION_EN_ATTENTE,
  );
  const briefs = runsEnAttente(executions);
  // Les questions d'agents (#1025), **appelées** et jamais recomptées ici : la
  // règle « qu'est-ce qui attend une réponse ? » vit dans `lib/questions`, comme
  // celle des briefs vit dans `lib/brief`.
  const demandes = questionsEnAttente(questions);
  const nb = enAttente.length + briefs.length + demandes.length;
  const notables = grouperEvenements(
    evenements.filter(estNotableNotification),
  ).slice(0, MAX_EVENEMENTS_NOTABLES);
  // **Dérivées du persisté** (#928, `lib/issueRun`) et non du flux temps réel
  // qui peuple `evenements` : celui-ci part vide à chaque chargement, donc une
  // fin arrivée pendant qu'on regardait ailleurs n'y serait plus — c'est-à-dire
  // exactement le cas que le troisième critère du ticket vise.
  const issues = issuesRecentes(executions, projet, MAX_ISSUES_RAPPELEES);
  const issuesNeuves = aDesIssuesNonLues(issues, issuesVues);

  // Clic à l'extérieur, `Échap` et focus d'entrée viennent du hook partagé
  // (#536). Les flèches, elles, ne s'y appliquent pas — et c'est le hook qui
  // le constate, en ne trouvant aucune entrée de menu : voir le changement de
  // rôle plus bas.
  const fermer = useCallback(() => setOuvert(false), []);
  useSurfaceDeroulee({ ouvert, fermer, conteneur, declencheur, surface });

  const etiquette = etiquetteCloche(
    enAttente.length,
    briefs.length,
    demandes.length,
    issuesNeuves,
  );

  /**
   * Ouvrir le panneau **acquitte** les fins de run (#928) : ce qu'on vient de
   * montrer est lu. Le repère est l'horodatage de la fin la plus récente, et
   * non « maintenant » : une fin qui arriverait pendant que le panneau est
   * ouvert reste neuve, ce qu'une horloge aurait avalé.
   */
  const basculer = () =>
    setOuvert((avant) => {
      if (!avant) {
        const derniere = issues.reduce(
          (max, issue) => {
            const fin = issue.execution.fin ?? "";
            return fin > max ? fin : max;
          },
          issuesVues,
        );
        if (derniere !== issuesVues) {
          ecrireIssuesVues(derniere);
          setIssuesVues(derniere);
        }
      }
      return !avant;
    });

  return (
    // `data-guide` : la visite guidée (#122) éclaire la cloche — et s'y replie
    // quand aucune validation n'est en attente sur le tableau de bord.
    <div ref={conteneur} data-guide="notifications" className="relative">
      <button
        ref={declencheur}
        type="button"
        onClick={basculer}
        aria-haspopup="dialog"
        aria-expanded={ouvert}
        aria-label={etiquette}
        className="relative block rounded-md p-2 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        <IconeNotifications className="size-5" />
        {nb > 0 && (
          // Décoratif : le compte est déjà dans l'`aria-label` du bouton.
          <span
            aria-hidden="true"
            className="absolute -top-0.5 -right-0.5 inline-flex min-w-4 items-center justify-center rounded-full bg-rose-600 px-1 text-[0.625rem] leading-4 font-semibold text-white"
          >
            {nb > 9 ? "9+" : nb}
          </span>
        )}
        {/* **Un point, jamais un second chiffre** (#928). La pastille ci-dessus
            répond « combien de choses m'attendent » (#322) et une fin de run
            n'attend rien : deux compteurs côte à côte obligeraient à en faire la
            somme, et le brief suspendu que #322 a fait entrer dans le premier y
            reperdrait sa place. Le point dit seulement « il y a du neuf », et il
            s'efface à l'ouverture. Décoratif comme la pastille : l'`aria-label`
            du bouton le dit en toutes lettres.
            Il se range **sous** la pastille quand les deux sont là — en bas à
            droite contre en haut à droite —, si bien qu'aucun des deux n'en
            masque un autre. */}
        {issuesNeuves && (
          <span
            aria-hidden="true"
            className="absolute -right-0.5 -bottom-0.5 size-2 rounded-pastille bg-positif ring-2 ring-surface"
          />
        )}
      </button>

      {ouvert && (
        // `dialog` et non `menu` (#536). Un `role="menu"` engage un contenu fait
        // d'entrées `menuitem` — le motif ARIA l'exige, et l'audit du lot 5
        // (#537) le vérifiera. Or ce panneau porte des sections, des titres, des
        // listes et des cartes à **deux boutons d'arbitrage chacune** : ce n'est
        // pas un menu, ça n'en a jamais été un, et le déclarer tel promettait au
        // lecteur d'écran une navigation aux flèches qui ne pouvait pas exister.
        // Non modal à dessein : on y prend une décision sans que la page se
        // fige derrière.
        <div
          ref={surface}
          role="dialog"
          aria-label="Notifications"
          tabIndex={-1}
          className="absolute top-full right-0 z-20 mt-2 flex max-h-[min(70vh,32rem)] w-80 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
        >
          <div className="flex items-center justify-between border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
            <span className="text-corps font-semibold">Notifications</span>
            {nb > 0 && (
              <BadgeEtat ton="alerte" className="chiffre">
                {nb} à valider
              </BadgeEtat>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {/* Les briefs d'abord : un run suspendu bloque **tout** le run,
                là où une validation ne retient qu'une tâche. */}
            {briefs.length > 0 && (
              <section aria-label="Briefs en attente" className="p-2">
                <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                  Briefs à trancher
                </h3>
                <ul className="space-y-2">
                  {briefs.map((run) => (
                    <li key={run.run_id}>
                      <CarteBriefCompacte
                        run={run}
                        surOuverture={() => setOuvert(false)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Les questions d'agents (#1025), **entre** les briefs et les
                validations. L'ordre du panneau suivait jusqu'ici ce que chaque
                attente bloque ; celle-ci s'y range sur une autre propriété, et
                c'est elle qui la fait passer devant les validations : c'est la
                seule des trois qui **périme**. Un brief et une validation
                attendent indéfiniment, une question a une borne — passée elle,
                l'agent est reparti sur son hypothèse, et la répondre ne le
                rattrapera qu'au prochain appel identique. */}
            {demandes.length > 0 && (
              // Sur les **jetons du socle** et au **barème**, et non sur les
              // `neutral-*`/`text-xs`/`p-2` que ses sections voisines portent
              // encore : une couleur se choisit une fois (docs/30 §2,
              // `couleurs.test.ts`), un pas typographique aussi (#981), une
              // densité aussi (#983). Le rendu est le même au pixel près —
              // `--text-xs` **est** `--text-annexe` — et les trois résidus, qui
              // ne peuvent que décroître, ne grandissent pas d'une ligne.
              // Replier les sections voisines est une migration, pas ce ticket.
              <section aria-label="Questions d'agents" className="p-2.5">
                <h3 className="px-1 pb-1 text-annexe font-semibold tracking-wide text-texte-secondaire uppercase">
                  Questions d&apos;agents
                </h3>
                <ul className="space-y-2">
                  {demandes.map((question) => (
                    <li key={question.question_id}>
                      <CarteQuestionCompacte
                        question={question}
                        surOuverture={() => setOuvert(false)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section aria-label="Validations en attente" className="p-2">
              <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                À valider
              </h3>
              {enAttente.length === 0 ? (
                <p className="px-1 py-1 text-xs text-neutral-500 dark:text-neutral-400">
                  Aucune validation en attente.
                </p>
              ) : (
                <ul className="space-y-2">
                  {enAttente.map((validation) => (
                    <li key={validation.tache_id}>
                      {/* **La** carte du produit, en densité compacte (#1228) —
                          plus une recopie resserrée. Elle apporte ici trois
                          choses que la cloche n'avait pas : l'**acte** en tête
                          quand il y en a un (#573 — « Rédiger le README »
                          s'affichait au-dessus d'un `rm -rf`), ses arguments ou
                          son diff, et le **motif** du refus, qui est ce qui
                          réoriente l'agent (#1185). */}
                      <CarteValidation
                        validation={validation}
                        decider={decider}
                        maintenant={maintenant}
                        densite="compacte"
                      />
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* **Ce que le travail a rendu** (#928) — après les deux files qui
                attendent un geste, avant l'activité récente qui n'en attend
                aucun : c'est l'ordre dans lequel on agit. Une fin de run n'est
                pas une demande, c'est un résultat, et le panneau la range donc
                entre les deux.

                Elle est **dérivée du persisté** et non du flux : c'est ce qui
                tient le troisième critère du ticket, et c'est aussi pourquoi
                `execution.statut` n'a **pas** été ajouté à
                `estNotableNotification` — le dire ici et en activité récente
                donnerait deux annonces de la même fin dans le même panneau,
                dont une qui disparaîtrait au rechargement. */}
            {issues.length > 0 && (
              // Sur les **jetons du socle** (`bord`, `texte-secondaire`) et non
              // sur les `neutral-*` que ses sections voisines portent encore :
              // une couleur se choisit une fois, les deux thèmes viennent avec
              // elle (docs/30 §2, `tests/couleurs.test.ts`). Le rendu est le
              // même au bit près — `--bord` **est** `neutral-200` en clair — et
              // le résidu du fichier ne grandit pas d'une ligne de plus.
              //
              // Même règle pour la taille et le pas, depuis que les trois lots
              // de #973 les ont mis au barème : `text-annexe` (#981) est le pas
              // de second plan — au pixel près le `text-xs` que les sections
              // voisines écrivent encore, `--text-xs` **étant** `--text-annexe`
              // — et `p-2.5` (#983) la densité « compacte » du socle. Seul le
              // padding bouge vraiment, de 8 à 10 px : `p-2` n'a pas de jumelle
              // au barème, et un écran neuf s'y replie plutôt que d'allonger un
              // résidu qui ne peut que décroître. Replier les trois sections
              // voisines avec lui est une migration, pas ce ticket.
              <section
                aria-label="Runs terminés"
                className="border-t border-bord p-2.5"
              >
                <h3 className="px-1 pb-1 text-annexe font-semibold tracking-wide text-texte-secondaire uppercase">
                  Ce que le travail a rendu
                </h3>
                <ul className="space-y-2">
                  {issues.map((issue) => (
                    <li key={issue.execution.run_id}>
                      <Carte densite="compacte">
                        <AnnonceIssueRun issue={issue} compacte />
                      </Carte>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* L'activité récente notable : le panneau reste consultable même
                quand plus aucune validation n'est en attente (critère #119). */}
            <section
              aria-label="Activité récente"
              className="border-t border-neutral-200 p-2 dark:border-neutral-800"
            >
              <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                Activité récente
              </h3>
              {notables.length === 0 ? (
                <p className="px-1 py-1 text-xs text-neutral-500 dark:text-neutral-400">
                  Rien de notable pour l&apos;instant.
                </p>
              ) : (
                <ol className="space-y-0.5">
                  {notables.map((groupe) => (
                    <LigneActivite key={groupe.cle} groupe={groupe} compact />
                  ))}
                </ol>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Un brief en attente, en version compacte : ce qu'il attend, depuis quand, et
 * le chemin vers l'écran qui le tranche.
 *
 * **Aucun bouton de décision ici**, contrairement à la carte de validation
 * ci-dessous, et c'est la seule différence qui compte : on n'approuve pas sept
 * sections, des questions et un coût depuis une pastille de 20 rem. Le panneau
 * se referme au clic — laisser une cloche ouverte par-dessus l'écran qu'elle
 * vient d'ouvrir masque justement ce qu'on est venu lire.
 *
 * Le chemin est celui du **fil** depuis #483 (`PAGE_DU_CADRAGE`), où le brief se
 * décide désormais — même raison qu'au panneau du tableau de bord : un renvoi
 * résolu par le menu s'éteint le jour où l'entrée part, et une cloche muette sur
 * un run bloqué est le défaut que le critère 3 interdit. L'entrée est partie le
 * 2026-08-28 (#484) sans que cette carte change.
 */
function CarteBriefCompacte({
  run,
  surOuverture,
}: {
  run: ResumeExecution;
  surOuverture: () => void;
}) {
  const page = entreeParLibelle(PAGE_DU_CADRAGE);
  const reponses = run.statut === EXECUTION_EN_ATTENTE_REPONSES;
  if (page === undefined) return null;

  return (
    <Carte densite="compacte" ton="attention">
      {/* Le titre court du run (#991), plus l'objectif entier en infobulle :
          ici le `title` apprend quelque chose, là où #536 l'a retiré des
          endroits où il redisait le texte visible. */}
      <p className="line-clamp-2 text-annexe font-medium" title={run.objectif}>
        {nomDuRun(run)}
      </p>
      <p className="mt-0.5 flex items-center gap-1 text-micro text-neutral-500 dark:text-neutral-400">
        <IconeBrief className="size-3 shrink-0" />
        {reponses
          ? "Des questions attendent vos réponses"
          : "Le brief attend votre décision"}
      </p>
      <Link
        href={page.href}
        onClick={surOuverture}
        className={`mt-2 inline-flex items-center gap-1 ${CIBLE_MINIMALE} text-micro font-medium text-amber-800 hover:underline dark:text-amber-300`}
      >
        {reponses ? "Répondre" : "Relire le brief"}
        <IconeFlecheDroite className="size-3 shrink-0" />
      </Link>
    </Carte>
  );
}

/**
 * Une question d'agent en version compacte : qui demande, ce qu'il demande, et
 * le chemin vers le fil où l'on y répond.
 *
 * **Aucun champ de réponse ici**, et c'est la décision de la veille du ticket :
 * une question se répond **dans le fil**, là où on a la conversation qui l'a
 * produite (#483, docs/29). Un second endroit où écrire en ferait deux, dont un
 * sans le contexte — et le canal n'a pas d'autre mémoire que son fil. Même
 * partage que `CarteBriefCompacte`, pour une raison voisine.
 *
 * Le chemin est un **libellé de menu** (`PAGE_DES_QUESTIONS`) et non une URL :
 * un renvoi codé en dur s'éteint le jour où la page déménage, et une cloche
 * muette sur un agent suspendu est exactement ce que le critère 2 interdit.
 */
function CarteQuestionCompacte({
  question,
  surOuverture,
}: {
  question: Question;
  surOuverture: () => void;
}) {
  const page = entreeParLibelle(PAGE_DES_QUESTIONS);
  if (page === undefined) return null;

  return (
    <Carte densite="compacte" ton="attention">
      {/* La question d'abord — c'est elle qu'on vient lire. L'infobulle porte le
          texte entier quand il est écrêté : ici le `title` apprend quelque
          chose, comme sur la carte de brief. */}
      <p className="line-clamp-2 text-annexe font-medium" title={question.question}>
        {question.question}
      </p>
      {/* Jetons du socle, pas de `neutral-*`/`amber-*` bruts — voir la section
          qui monte cette carte : les deux thèmes viennent avec le token, et le
          banc de contraste ne juge que les tokens (docs/30 §1.6, §2). */}
      <p className="mt-0.5 flex items-center gap-1 text-micro text-texte-secondaire">
        <IconeAgent className="size-3 shrink-0" />
        Agent {question.agent}
        {question.role ? ` · ${question.role}` : ""}
      </p>
      {question.hypothese && (
        <p className="mt-1 line-clamp-2 text-micro text-attention-texte italic">
          Sans réponse : {question.hypothese}
        </p>
      )}
      <Link
        href={page.href}
        onClick={surOuverture}
        className={`mt-2 inline-flex items-center gap-1 ${CIBLE_MINIMALE} text-micro font-medium text-attention-texte hover:underline`}
      >
        Répondre dans le fil
        <IconeFlecheDroite className="size-3 shrink-0" />
      </Link>
    </Carte>
  );
}
