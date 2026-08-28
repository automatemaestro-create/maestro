"use client";

/**
 * Le Markdown d'un message de l'agent, **rendu** (#697, lot 7 de #690).
 *
 * L'analyse vit à côté (`lib/markdown`) et rend un arbre de données ; ce fichier
 * ne fait que le traduire en éléments React. La séparation n'est pas de la
 * symétrie : c'est elle qui donne au premier avertissement du ticket une réponse
 * **structurelle** — il n'y a nulle part une chaîne de HTML à assainir, donc
 * aucun `dangerouslySetInnerHTML` à écrire, donc rien qu'une liste d'éléments
 * autorisés doive rattraper. Ce qu'un modèle écrirait en HTML (`<img
 * onerror=…>`) ressort en toutes lettres, comme le reste de son texte.
 *
 * ## Deux décisions de fond
 *
 * **1. Un titre du message n'est pas un titre du document.** `## Étapes` rend un
 * paragraphe en gras, jamais un `<h2>`. Le plan d'un écran est une propriété de
 * l'écran (un `<h1>`, aucun saut de niveau — docs/30 §2.1, gardé par
 * `a11y.test.tsx`), et laisser le contenu d'un modèle y insérer des niveaux
 * ferait deux dégâts d'un coup : la règle `heading-order` d'axe rougirait au
 * premier `###` d'un agent, et le sommaire annoncé au lecteur d'écran
 * décrirait la réponse plutôt que la page. Le poids visuel est rendu ; l'autorité
 * sur la structure, non. C'est le même partage que pour les liens — on rend ce
 * qui se lit, on ne cède pas ce qui engage.
 *
 * **2. La bulle de l'utilisateur ne passe pas par ici.** Le critère vise « les
 * bulles de l'agent », et ce n'est pas une économie : ce que quelqu'un a tapé
 * doit se relire **tel qu'il l'a tapé**, astérisques comprises. Rendre son
 * message en Markdown lui ferait dire autre chose que ce qu'il a écrit, sur la
 * seule surface du produit où il est l'auteur.
 *
 * ## Le curseur du direct
 *
 * `curseur` est ce que la réponse en cours d'écriture pose à la fin du texte
 * reçu (#695). Il est **fondu dans le dernier bloc** au lieu d'être posé après :
 * un élément de bloc ajouté sous le paragraphe le ferait tomber à la ligne, puis
 * disparaître à la clôture du flux — deux sauts pour une décoration. Fondu, il
 * suit la dernière lettre et s'efface sans rien déplacer.
 */

import { Fragment, type ReactNode } from "react";

import { BlocDeCode } from "@/components/chat/BlocDeCode";
import { analyserMarkdown, type Bloc, type Inline } from "@/lib/markdown";

/**
 * Le poids d'un titre de message. Deux pas seulement, et pris à l'échelle du
 * socle (`globals.css`) : un `#` ou un `##` ouvrent une partie, tout ce qui est
 * plus bas est un intertitre. Six graisses distinctes dans une bulle de
 * conversation seraient six façons de ne rien hiérarchiser.
 */
const TITRE = (niveau: number) =>
  niveau <= 2
    ? "mt-3 mb-1 text-titre font-semibold text-texte first:mt-0"
    : "mt-2 mb-1 text-corps font-semibold text-texte first:mt-0";

export function TexteMarkdown({
  texte,
  curseur,
}: {
  texte: string;
  /** Le témoin d'une réponse qui continue de s'écrire — fondu au dernier bloc. */
  curseur?: ReactNode;
}) {
  const blocs = analyserMarkdown(texte);
  // Rien à rendre, mais un curseur à poser : c'est l'instant entre la trame
  // d'ouverture et le premier mot. L'appelant, lui, décide déjà de ne pas
  // monter de bulle vide (#695) ; ici on se contente de ne pas perdre le témoin.
  if (blocs.length === 0) return curseur === undefined ? null : <p>{curseur}</p>;

  return (
    <div className="flex flex-col">
      {blocs.map((bloc, rang) => (
        <RenduBloc
          key={rang}
          bloc={bloc}
          curseur={rang === blocs.length - 1 ? curseur : undefined}
        />
      ))}
    </div>
  );
}

function RenduBloc({
  bloc,
  curseur,
}: {
  bloc: Bloc;
  curseur?: ReactNode;
}) {
  switch (bloc.type) {
    case "paragraphe":
      return (
        <p className="break-words [&:not(:first-child)]:mt-2">
          <RenduInlines noeuds={bloc.enfants} />
          {curseur}
        </p>
      );

    case "titre":
      return (
        <p className={`break-words ${TITRE(bloc.niveau)}`}>
          <RenduInlines noeuds={bloc.enfants} />
          {curseur}
        </p>
      );

    case "code":
      return (
        <>
          <BlocDeCode
            texte={bloc.texte}
            langage={bloc.langage}
            ferme={bloc.ferme}
          />
          {/* Le curseur sort du bloc de code : dedans, il ferait partie de ce
              qu'on copie. */}
          {curseur !== undefined && <p>{curseur}</p>}
        </>
      );

    case "liste": {
      // `ps-5` et non `ps-4` : la puce vit **dans** le retrait, et un retrait
      // trop court la colle au bord de la bulle.
      const classes =
        "my-1 flex flex-col gap-0.5 ps-5 " +
        (bloc.ordonnee ? "list-decimal" : "list-disc");
      const entrees = bloc.entrees.map((entree, rang) => (
        <li key={rang} className="break-words">
          <RenduInlines noeuds={entree} />
          {curseur !== undefined && rang === bloc.entrees.length - 1 && curseur}
        </li>
      ));
      return bloc.ordonnee ? (
        <ol className={classes} start={bloc.depart}>
          {entrees}
        </ol>
      ) : (
        <ul className={classes}>{entrees}</ul>
      );
    }

    case "citation":
      return (
        <blockquote className="my-2 border-s-2 border-bord ps-3 text-texte-secondaire">
          {bloc.blocs.map((interne, rang) => (
            <RenduBloc
              key={rang}
              bloc={interne}
              curseur={rang === bloc.blocs.length - 1 ? curseur : undefined}
            />
          ))}
        </blockquote>
      );

    case "filet":
      return <hr className="my-3 border-bord" />;
  }
}

function RenduInlines({ noeuds }: { noeuds: Inline[] }) {
  return (
    <>
      {noeuds.map((noeud, rang) => (
        <Fragment key={rang}>
          <RenduInline noeud={noeud} />
        </Fragment>
      ))}
    </>
  );
}

function RenduInline({ noeud }: { noeud: Inline }) {
  switch (noeud.type) {
    case "texte":
      return <>{noeud.texte}</>;

    case "saut":
      return <br />;

    case "code":
      // La chasse fixe **et** l'aplat en retrait : `run_id` doit se distinguer du
      // mot d'à côté même en noir et blanc. `surface-creuse` est le seul aplat du
      // socle qui se creuse dans les deux thèmes, donc le seul qui ne s'inverse
      // pas d'un thème à l'autre.
      return (
        <code className="rounded border border-bord bg-surface-creuse px-1 font-mono">
          {noeud.texte}
        </code>
      );

    case "fort":
      return (
        <strong className="font-semibold">
          <RenduInlines noeuds={noeud.enfants} />
        </strong>
      );

    case "accent":
      return (
        <em>
          <RenduInlines noeuds={noeud.enfants} />
        </em>
      );

    case "lien":
      // `href` a déjà passé `lienExterneSur` (`lib/markdown`) : il est absolu et
      // d'un schéma suivable. Le reste est la conduite du produit pour un lien
      // qui sort (`LienTicketExterne`) — nouvel onglet, et la page ouverte
      // n'obtient aucune prise sur celle-ci.
      return (
        <a
          href={noeud.href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent-texte underline underline-offset-2"
        >
          <RenduInlines noeuds={noeud.enfants} />
        </a>
      );
  }
}
