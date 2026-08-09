"""Détail d'une tâche : description, étapes et liens (#246, tests différés au lot 8).

Le lot 2 de la vague (#242) a posé le modèle et sa projection **sans aucun
test** — ce fichier est sa couverture, pas un complément.

Ce qui se teste ici est la **chaîne entière**, parce que c'est elle que le
contrat (#183) décrit et qu'aucun de ses maillons ne dit la vérité seul :

    consigne_detail → ligne `<tache>:detail` du journal (#8)
                    → `evenements_depuis_step` → événement `tache.detail` (#46)
                    → `ControlTowerState.appliquer` → la carte (#251)

Deux règles gouvernent tout le module, et ce sont celles du contrat :

- **rien ne s'invente** — une étape sans libellé, un lien sans libellé ni URL
  suivable n'apprennent rien et sont écartés plutôt que rendus en blanc ;
- **rien ne se refuse** — la validation ne lève jamais et ne fait pas échouer un
  run : un état inconnu reste tel quel, une URL de schéma inattendu est jetée en
  gardant le libellé.

La troisième, celle qui rend le rejeu du journal durable (#97) sûr, est
l'**idempotence** : `None` (« l'étape n'en dit rien ») et `[]` (« plus aucune
étape ») sont deux choses différentes tout au long de la chaîne, et c'est ce qui
permet au flot ordinaire des `tache.statut` de traverser une tâche renseignée
sans vider son panneau.
"""

import pytest

from maestro.controltower import (
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    Event,
)
from maestro.controltower.bridge import evenements_depuis_step

# `EVENEMENT_TACHE_DETAIL` n'est pas ré-exporté par `maestro.controltower`, à la
# différence de tous ses voisins (`…_TACHE_STATUT`, `…_TACHE_REASSIGNATION`…) :
# on le prend donc à sa source plutôt que d'élargir la surface publique depuis
# un lot « tests + doc ».
from maestro.controltower.events import EVENEMENT_TACHE_DETAIL
from maestro.detail_tache import (
    ETAPE_A_FAIRE,
    ETAPE_FAITE,
    LONGUEUR_MAX_LIBELLE,
    SUFFIXE_ETAPE_DETAIL,
    EtapeTache,
    LienUtile,
    consigne_detail,
    etapes_depuis,
    liens_depuis,
)
from maestro.telemetry.journal import RunJournal

# --- ① Le modèle : rien ne s'invente ------------------------------------------------


def test_etape_et_lien_font_l_aller_retour():
    """`to_dict`/`from_dict` conservent l'étape et le lien à l'identique."""
    etape = EtapeTache(libelle="Écrire les tests", etat=ETAPE_FAITE)
    assert EtapeTache.from_dict(etape.to_dict()) == etape

    lien = LienUtile(libelle="Écran", url="https://figma.test/f/1", nature="maquette")
    assert LienUtile.from_dict(lien.to_dict()) == lien


def test_une_etape_sans_libelle_est_ecartee():
    """L'état seul ne fait pas une étape : une case sans énoncé fausserait l'avancement."""
    assert etapes_depuis([{"etat": ETAPE_FAITE}]) == []
    assert etapes_depuis([{"libelle": "   ", "etat": ETAPE_FAITE}]) == []


def test_un_lien_sans_libelle_ni_url_est_ecarte():
    """La nature seule ne fait pas un lien : il ne resterait que ce que l'icône dit déjà."""
    assert liens_depuis([{"nature": "maquette"}]) == []


def test_un_lien_garde_son_libelle_meme_sans_url_suivable():
    """`libelle` sans `url` reste une **mention** : c'est l'un ou l'autre qui
    manque, jamais les deux."""
    assert liens_depuis([{"libelle": "Sans URL"}]) == [LienUtile(libelle="Sans URL")]


def test_l_ordre_du_flux_est_celui_de_la_checklist():
    """Une checklist se lit dans l'ordre où elle a été posée, jamais trié."""
    etapes = etapes_depuis(
        [{"libelle": "Trois"}, {"libelle": "Un"}, {"libelle": "Deux"}]
    )
    assert [etape.libelle for etape in etapes] == ["Trois", "Un", "Deux"]


def test_une_entree_illisible_ne_fait_pas_perdre_les_autres():
    """L'écart se fait ligne à ligne : une entrée cassée n'emporte pas la liste."""
    assert etapes_depuis(["pas un dict", None, {"libelle": "Seule survivante"}]) == [
        EtapeTache(libelle="Seule survivante")
    ]


def test_une_chaine_n_est_pas_iteree_caractere_par_caractere():
    """Une chaîne **est** une `Sequence` : l'itérer rendrait une entrée par lettre."""
    assert etapes_depuis("abc") == []
    assert liens_depuis("abc") == []


@pytest.mark.parametrize("brut", [None, 42, {"libelle": "pas une liste"}])
def test_une_valeur_qui_n_est_pas_une_liste_rend_une_liste_vide(brut):
    """Ce qui n'est pas une séquence n'apprend rien — et ne lève pas."""
    assert etapes_depuis(brut) == []
    assert liens_depuis(brut) == []


# --- ① Le modèle : rien ne se refuse ------------------------------------------------


def test_un_etat_inconnu_reste_tel_quel():
    """Le front le ramènera à « à faire » — même garde que la colonne « Autres »."""
    (etape,) = etapes_depuis([{"libelle": "Venue d'ailleurs", "etat": "inedit"}])
    assert etape.etat == "inedit"


def test_un_etat_absent_ou_vide_retombe_sur_a_faire():
    """Le défaut du contrat, sans quoi une étape muette n'aurait pas d'avancement."""
    assert etapes_depuis([{"libelle": "Muette"}])[0].etat == ETAPE_A_FAIRE
    assert etapes_depuis([{"libelle": "Vide", "etat": ""}])[0].etat == ETAPE_A_FAIRE


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "../relatif", "https://x/ avec espace", "data:text/html,x"],
)
def test_une_url_de_schema_inattendu_est_jetee_en_gardant_le_libelle(url):
    """Un lien mort vaut mieux détruit qu'affiché — et un run n'échoue pas pour autant."""
    (lien,) = liens_depuis([{"libelle": "Douteux", "url": url}])
    assert lien.libelle == "Douteux"
    assert lien.url == ""


def test_le_libelle_est_normalise_et_borne():
    """Une ligne de checklist, pas un paragraphe collé par accident."""
    (etape,) = etapes_depuis([{"libelle": "  trop   d'espaces\n ici  "}])
    assert etape.libelle == "trop d'espaces ici"

    (longue,) = etapes_depuis([{"libelle": "x" * (LONGUEUR_MAX_LIBELLE + 50)}])
    assert len(longue.libelle) == LONGUEUR_MAX_LIBELLE


def test_l_etat_et_la_nature_sont_ramenes_en_minuscules():
    """Deux graphies du même état ne doivent pas faire deux états."""
    assert etapes_depuis([{"libelle": "L", "etat": "FAITE"}])[0].etat == ETAPE_FAITE
    assert liens_depuis([{"libelle": "L", "nature": "Maquette"}])[0].nature == "maquette"


# --- ② Le journal : ce qui n'apprend rien n'est pas consigné -------------------------


def test_consigne_detail_pose_une_etape_annexe_rattachee_a_la_tache():
    """La ligne porte le suffixe `:detail` et un résumé lisible par le fil d'activité."""
    journal = RunJournal(run_id="run-detail")
    consigne_detail(
        journal,
        "t1",
        description="Couvrir le panneau.",
        etapes=[{"libelle": "Lire le ticket", "etat": ETAPE_FAITE}],
        liens=[{"libelle": "Écran", "url": "https://figma.test/f/1", "nature": "maquette"}],
    )

    (record,) = journal.records
    assert record.etape == f"t1{SUFFIXE_ETAPE_DETAIL}"
    assert record.description == "Couvrir le panneau."
    assert [etape.libelle for etape in record.etapes] == ["Lire le ticket"]
    assert [lien.nature for lien in record.liens] == ["maquette"]
    assert record.sortie == "détail de tâche : description, 1 étape(s), 1 lien(s)"


def test_poser_un_detail_ne_depense_rien():
    """Le moteur ne voit qu'une étape de plus : rien n'entre au grand livre."""
    journal = RunJournal(run_id="run-gratuit")
    consigne_detail(journal, "t1", description="Gratuit.")

    (record,) = journal.records
    assert record.usage.cout_usd in (None, 0, 0.0)


@pytest.mark.parametrize(
    ("tache_id", "kwargs"),
    [
        ("t1", {}),
        ("t1", {"description": "   "}),
        ("t1", {"etapes": [{"etat": ETAPE_FAITE}]}),
        ("t1", {"liens": [{"nature": "maquette"}]}),
        ("", {"description": "Sans tâche."}),
    ],
)
def test_un_detail_qui_n_apprend_rien_n_est_pas_consigne(tache_id, kwargs):
    """Il n'y a rien à montrer : pas de ligne, donc pas d'événement ni de carte touchée."""
    journal = RunJournal(run_id="run-muet")
    consigne_detail(journal, tache_id, **kwargs)
    assert journal.records == ()


def test_la_ligne_rend_null_ce_qui_n_est_pas_renseigne():
    """Le crux de l'idempotence : `null` et non `""`/`[]`, sinon chaque ligne
    de journal effacerait le détail posé par la précédente."""
    journal = RunJournal(run_id="run-partiel")
    consigne_detail(journal, "t1", description="Seule la description.")

    ligne = journal.records[0].to_dict()
    assert ligne["description"] == "Seule la description."
    assert ligne["etapes"] is None
    assert ligne["liens"] is None


# --- ③ Le pont : la ligne devient un événement `tache.detail` ------------------------


def test_la_ligne_de_detail_devient_un_evenement_tache_detail():
    """Le pont (#46) reconnaît le suffixe et rend la tâche à laquelle il se rattache."""
    journal = RunJournal(run_id="run-pont")
    consigne_detail(
        journal,
        "t1",
        description="Comprendre la tâche.",
        etapes=[{"libelle": "Une étape"}],
    )

    (event,) = evenements_depuis_step(journal.records[0].to_dict())
    assert event.type == EVENEMENT_TACHE_DETAIL
    assert event.tache_id == "t1"
    assert event.description == "Comprendre la tâche."
    assert [etape.libelle for etape in event.etapes] == ["Une étape"]
    # Renseigner une tâche ne dépense rien : pas de mesure à faire entrer au
    # grand livre, comme pour la référence de ticket (#187).
    assert event.usage is None
    assert event.cout_usd is None


def test_l_evenement_de_detail_fait_l_aller_retour_json():
    """Ce qui traverse le bus doit revenir identique — le rejeu en dépend."""
    event = Event(
        type=EVENEMENT_TACHE_DETAIL,
        tache_id="t1",
        description="Aller-retour.",
        etapes=[EtapeTache(libelle="Une étape", etat=ETAPE_FAITE)],
        liens=[LienUtile(libelle="Écran", url="https://figma.test/f/1", nature="maquette")],
    )
    relu = Event.from_dict(event.to_dict())
    assert relu.description == event.description
    assert relu.etapes == event.etapes
    assert relu.liens == event.liens


def test_une_ligne_ordinaire_ne_parle_pas_de_detail():
    """`None` sur une étape sans détail : la projection ne devra toucher à rien."""
    (event,) = evenements_depuis_step({"run_id": "r1", "etape": "t1", "statut": "en_cours"})
    assert event.type == EVENEMENT_TACHE_STATUT
    assert event.etapes is None
    assert event.liens is None


# --- ④ La projection : poser, ne pas effacer, et rejouer sans dériver ----------------


def _detail(**kwargs) -> Event:
    """Un événement `tache.detail` sur `t1`, complété par `kwargs`."""
    return Event(type=EVENEMENT_TACHE_DETAIL, run_id="r1", tache_id="t1", **kwargs)


def test_le_detail_arrive_sur_la_carte():
    """Ce que le panneau (#251) lit vient de là, et de nulle part ailleurs."""
    state = ControlTowerState()
    state.appliquer(
        _detail(
            description="Couvrir le panneau.",
            etapes=[EtapeTache(libelle="Lire le ticket", etat=ETAPE_FAITE)],
            liens=[LienUtile(libelle="Écran", url="https://figma.test/f/1", nature="maquette")],
        )
    )

    carte = state.tache("t1").to_dict()
    assert carte["description"] == "Couvrir le panneau."
    assert carte["etapes"] == [{"libelle": "Lire le ticket", "etat": ETAPE_FAITE}]
    assert carte["liens"] == [
        {"libelle": "Écran", "url": "https://figma.test/f/1", "nature": "maquette"}
    ]


def test_la_tache_est_creee_si_elle_est_encore_inconnue():
    """L'événement peut précéder la première étape consignée."""
    state = ControlTowerState()
    state.appliquer(_detail(description="Arrivée en premier."))
    assert state.tache("t1").description == "Arrivée en premier."


def test_le_detail_ne_fait_pas_bouger_la_tache_d_une_colonne():
    """Renseigner une tâche n'est pas la faire avancer : le statut ne bouge pas."""
    state = ControlTowerState()
    state.appliquer(
        Event(type=EVENEMENT_TACHE_STATUT, run_id="r1", tache_id="t1", statut="en_cours")
    )
    state.appliquer(_detail(description="Un détail."))
    assert state.tache("t1").statut == "en_cours"


def test_le_projet_voyage_sur_l_evenement_de_detail():
    """L'appartenance au projet (#222) voyage sur tous les événements de tâche, celui-ci compris."""
    state = ControlTowerState()
    state.appliquer(_detail(description="Un détail.", projet_id="p1"))
    assert state.tache("t1").projet_id == "p1"


def test_une_tache_sans_detail_expose_des_listes_vides():
    """Rien ne s'invente — et les deux formes du vide sont distinctes (#246).

    `null` pour une description absente, `[]` pour les listes : le client
    distingue ainsi « rien à montrer » d'une clé qu'il ne connaîtrait pas, et le
    panneau de détail reste fermé.
    """
    state = ControlTowerState()
    state.appliquer(Event(type=EVENEMENT_TACHE_STATUT, tache_id="t1", statut="en_cours"))

    carte = state.tache("t1").to_dict()
    assert carte["etapes"] == []
    assert carte["liens"] == []
    assert carte["description"] is None


def test_le_flot_ordinaire_des_statuts_ne_vide_pas_un_panneau_renseigne():
    """Le cœur du contrat : `None` dit « je n'en parle pas », et ne retire rien."""
    state = ControlTowerState()
    state.appliquer(
        _detail(
            description="À conserver.",
            etapes=[EtapeTache(libelle="Une étape")],
            liens=[LienUtile(libelle="Écran", url="https://figma.test/f/1")],
        )
    )
    # Un `tache.statut` ne porte aucun détail : il ne doit rien emporter.
    state.appliquer(
        Event(type=EVENEMENT_TACHE_STATUT, run_id="r1", tache_id="t1", statut="terminee")
    )

    tache = state.tache("t1")
    assert tache.description == "À conserver."
    assert [etape.libelle for etape in tache.etapes] == ["Une étape"]
    assert [lien.libelle for lien in tache.liens] == ["Écran"]


def test_une_liste_presente_mais_vide_efface():
    """`[]` est une information — « plus aucune étape » — là où `None` n'en est pas une."""
    state = ControlTowerState()
    state.appliquer(_detail(etapes=[EtapeTache(libelle="Une étape")]))
    state.appliquer(_detail(etapes=[]))
    assert state.tache("t1").etapes == []


def test_poser_deux_fois_le_meme_detail_est_sans_effet():
    """L'idempotence qui rend le rejeu du journal durable (#97) sûr."""
    state = ControlTowerState()
    event = _detail(
        description="Deux fois.",
        etapes=[EtapeTache(libelle="Une étape", etat=ETAPE_FAITE)],
        liens=[LienUtile(libelle="Écran", url="https://figma.test/f/1", nature="maquette")],
    )

    state.appliquer(event)
    apres_une_fois = state.tache("t1").to_dict()
    state.appliquer(event)

    assert state.tache("t1").to_dict() == apres_une_fois


def test_le_rejeu_du_journal_rend_la_meme_carte():
    """Rejouer la ligne consignée reconstruit le panneau à l'identique, redémarrage compris."""
    journal = RunJournal(run_id="run-rejeu")
    consigne_detail(
        journal,
        "t1",
        description="Survivre au redémarrage.",
        etapes=[{"libelle": "Lire le ticket", "etat": ETAPE_FAITE}],
        liens=[{"libelle": "Écran", "url": "https://figma.test/f/1", "nature": "maquette"}],
    )
    ligne = journal.records[0].to_dict()

    def carte_apres(rejeux: int) -> dict:
        state = ControlTowerState()
        for _ in range(rejeux):
            for event in evenements_depuis_step(ligne):
                state.appliquer(event)
        return state.tache("t1").to_dict()

    assert carte_apres(1)["description"] == "Survivre au redémarrage."
    assert carte_apres(3) == carte_apres(1)


def test_un_detail_ulterieur_complete_sans_ecraser_ce_qu_il_ne_dit_pas():
    """Champ à champ : une description neuve n'emporte pas les étapes déjà posées."""
    state = ControlTowerState()
    state.appliquer(_detail(etapes=[EtapeTache(libelle="Une étape")]))
    state.appliquer(_detail(description="Ajoutée après coup."))

    tache = state.tache("t1")
    assert tache.description == "Ajoutée après coup."
    assert [etape.libelle for etape in tache.etapes] == ["Une étape"]
