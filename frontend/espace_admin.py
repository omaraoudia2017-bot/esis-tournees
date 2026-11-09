"""Espace administrateur (module 4), en 5 rubriques :
  - Tournées : importer un fichier de clients -> tournées L1, L2… réalisables -> visualiser, choisir la date,
    le livreur (et si besoin la période, l'heure de départ, les clients) -> attribuer ;
  - Planning : semaine par livreur, attribution en un clic, suivi en direct, changement de livreur ;
  - Livreurs (comptes, codes d'activation, camions) ; Clients (corrections, RGPD) ;
  - Tableau de bord et sécurité (indicateurs, alertes de l'IA, journal).

Appelé par app.py, qui fournit les outils communs (appel à l'API, session, carte de tournée).
Toutes les règles (droits, refus, journal) sont appliquées par l'API : cet écran ne fait qu'afficher.
"""
import json
from datetime import date, datetime, timedelta, timezone

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

ONGLETS = ["Tournées", "Planning", "Livreurs", "Clients", "Tableau de bord et sécurité"]
STATUTS_TOURNEE = {"planifiee": "Planifiée", "en_cours": "En cours", "terminee": "Terminée"}
LIBELLES_STATUT = {"a_livrer": "À livrer", "en_cours": "En cours", "livre": "Livré", "absent": "Absent",
                   "refuse": "Refusé", "reporte": "À reporter"}
TYPES_LIVREUR = {"Livreur interne": "interne", "Livreur prestataire": "prestataire"}
LIBELLES_PERIODE = {"matin": "Matin (7 h – 14 h)", "apres-midi": "Après-midi (13 h – 19 h)",
                    "soiree": "Soirée (17 h – 22 h)", "journee": "Journée complète (7 h – 22 h)"}
EVENEMENTS = ["(tous)", "CONNEXION_ANORMALE", "CONNEXION_ECHEC", "CONNEXION_REUSSIE", "COMPTE_VERROUILLE",
              "ACCES_REFUSE", "IMPORT_CSV", "ADMIN_CREATION_COMPTE", "ADMIN_DESACTIVER",
              "ADMIN_REINITIALISER_MDP", "ADMIN_REINITIALISER_2FA", "ADMIN_ATTRIBUTION_TOURNEE",
              "ADMIN_CORRECTION_CLIENT", "RGPD_EXPORT", "RGPD_ANONYMISATION", "AJOUT_CLIENT_VOLEE",
              "CONSULTATION_TOURNEE", "POINTAGE_ANNULE", "REMARQUE_AJOUTEE", "REMARQUE_SUPPRIMEE",
              "CONDITIONS_ACCEPTEES", "ADMIN_TYPE_LIVREUR", "DOMICILE_MODIFIE", "DOMICILE_SUPPRIME",
              "TRAJET_ETAPE_AJOUTEE", "TRAJET_ETAPE_SUPPRIMEE", "TRAJETS_OPTIMISES",
              "CODE_ACTIVATION_VALIDE", "MOT_DE_PASSE_DEFINI", "ADMIN_DESATTRIBUTION_TOURNEE",
              "ADMIN_REATTRIBUTION_TOURNEE", "TOURNEES_L_GENEREES", "TOURNEE_L_MODIFIEE", "TOURNEE_L_SUPPRIMEE",
              "TOURNEE_L_ATTRIBUEE"]

outils = None          # fourni par app.py : appel_api, reinitialiser, carte_tournee, heure_locale


# --------------------------------------------------------------------------- appels à l'API
def api(methode: str, chemin: str, attendu=(200,), timeout: int = 15, **kwargs):
    """Appel authentifié. Renvoie le JSON si le code est attendu, sinon affiche l'erreur et renvoie None."""
    r = outils.appel_api(methode, chemin, st.session_state.jeton, timeout=timeout, **kwargs)
    if r is None:
        return None
    if r.status_code == 401:
        outils.reinitialiser("Votre session a expiré. Veuillez vous reconnecter.")
        st.rerun()
    if r.status_code not in attendu:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, list):          # erreur de validation : premier champ en cause
            detail = "Saisie invalide : " + ", ".join(str(d.get("msg", "")) for d in detail[:2])
        st.error(detail or f"Erreur {r.status_code}.")
        return None
    return r.json()


def annoncer(type_message: str, texte: str) -> None:
    """Message affiché après le rechargement de la page."""
    st.session_state.message_admin = (type_message, texte)
    st.rerun()


def nombre_fr(valeur: float, signe: bool = False) -> str:
    """2.5 -> « 2,5 » ; avec signe : « +0,1 » / « −1,0 »."""
    texte = f"{valeur:+.1f}" if signe else f"{valeur:.1f}"
    return texte.replace(".", ",").replace("-", "−")


def date_fr(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "—"


def date_heure(iso: str | None) -> str:
    return datetime.fromisoformat(iso).astimezone(outils.fuseau).strftime("%d/%m %H:%M") if iso else "—"


# --------------------------------------------------------------------------- tableau de bord
def onglet_tableau_de_bord() -> None:
    jours = st.segmented_control("Période", [7, 30, 90], default=30, format_func=lambda j: f"{j} jours",
                                 key="td_jours") or 30
    td = api("GET", "/admin/tableau-de-bord", params={"jours": jours})
    if td is None:
        return
    st.caption(f"Du {date_fr(td['debut'])} au {date_fr(td['fin'])}")

    def pct(valeur):
        return f"{valeur:.0f} %" if valeur is not None else "—"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tournées", td["nb_tournees"])
    c2.metric("Livraisons", td["nb_livraisons"])
    c3.metric("Créneaux respectés", pct(td["taux_respect_creneaux"]),
              help=f"Arrivée chez le client dans son créneau ({td['creneaux_controles']} passages pointés)")
    c4.metric("Remplissage", pct(td["taux_remplissage"]),
              help="Clients par tournée rapportés à la limite d'effectif de la zone (10 à 20)")
    c1, c2, c3, c4 = st.columns(4)
    predit, fixe = td["ecart_moyen_predit_min"], td["ecart_moyen_fixe_min"]
    c1.metric("Écart temps sur place", f"{nombre_fr(predit)} min" if predit is not None else "—",
              # signe « - » ASCII : Streamlit en déduit le sens de la flèche
              delta=f"{nombre_fr(predit - fixe, True).replace('−', '-')} min vs valeurs fixes"
              if predit is not None and fixe is not None else None,
              delta_color="inverse",
              help=f"|réel − prédit| moyen sur {td['pointages_retenus']} pointages (1 à 90 min). "
                   f"Valeurs fixes 10 / 20 min : {nombre_fr(fixe) if fixe is not None else '—'} min.")
    c2.metric("Biais", f"{nombre_fr(td['biais_predit_min'], True)} min" if td["biais_predit_min"] is not None else "—",
              help="Positif : le modèle sous-estime le temps sur place")
    c3.metric("Alertes IA (connexions)", td["alertes_connexion"])
    c4.metric("Comptes verrouillés", td["comptes_verrouilles"],
              help=f"{td['echecs_connexion']} échecs de connexion sur la période")

    if td["par_jour"]:
        st.subheader("Évolution par jour")
        df = pd.DataFrame(td["par_jour"])
        df["date"] = pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date")[["respect_creneaux", "remplissage"]]
                      .rename(columns={"respect_creneaux": "Créneaux respectés (%)",
                                       "remplissage": "Remplissage (%)"}), height=260)
    if td["statuts"]:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("Issues des passages")
            st.bar_chart(pd.Series({LIBELLES_STATUT.get(k, k): v for k, v in td["statuts"].items()},
                                   name="Livraisons"), height=260, horizontal=True)
        with col2:
            st.subheader("Tournées")
            st.dataframe(pd.DataFrame([{
                "Date": date_fr(t["date"]), "Livreur": t["livreur"],
                "Statut": STATUTS_TOURNEE.get(t["statut"], t["statut"]),
                "Clients": f"{t['clients']} / {t['limite']}", "Pointés": t["faites"],
                "Remplissage (%)": t["remplissage"],
                "km": nombre_fr(t["distance_km"]) if t["distance_km"] is not None else "—"} for t in reversed(td["par_tournee"])]),
                hide_index=True, width="stretch", height=260)
    else:
        st.info("Aucune tournée sur la période.")
    st.caption("Le pointage sert à améliorer l'estimation des temps de livraison, pas à évaluer "
               "la performance individuelle des livreurs.")


# --------------------------------------------------------------------------- outils communs (livreurs, suivi)
JOURS_SEMAINE = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def occupation(jour: date) -> dict[str, dict]:
    """Identifiant du livreur -> sa tournée ce jour-là (pour signaler les livreurs déjà occupés)."""
    tournees = api("GET", "/admin/tournees", params={"date": jour.isoformat()}) or []
    return {t["livreur"]: t for t in tournees if t["livreur"]}


def livreurs_actifs() -> list[dict]:
    return [c for c in (api("GET", "/admin/utilisateurs") or []) if c["role"] == "livreur" and c["actif"]]


def libelle_livreur(c: dict, occupes: dict[str, dict], jour: date) -> str:
    camion = f"{c['vehicule']} ({c['type_camion']})" if c["vehicule"] else "sans camion"
    t = occupes.get(c["identifiant"])
    etat = f" — DÉJÀ une tournée le {jour:%d/%m} ({t['nb_clients']} clients)" if t else " — libre"
    return f"{c['prenom']} {c['nom']} — {camion}{etat}"


def donner_a_un_livreur(t: dict, cle: str, sauf: str | None = None) -> None:
    """Choix d'un livreur pour une tournée existante (en attente ou à transférer)."""
    jour = date.fromisoformat(t["date_tournee"])
    occupes = occupation(jour)
    candidats = [c for c in livreurs_actifs() if c["identifiant"] != sauf]
    libres = sorted(candidats, key=lambda c: c["identifiant"] in occupes)       # libres en premier
    col1, col2 = st.columns([3, 1])
    choix = col1.selectbox("Donner la tournée à", libres, index=None, key=f"donner_{cle}",
                           placeholder="Choisir un livreur",
                           format_func=lambda c: libelle_livreur(c, occupes, jour))
    if choix and choix["identifiant"] in occupes:
        st.warning(f"{choix['prenom']} {choix['nom']} a déjà une tournée le {jour:%d/%m/%Y} : annulez-la d'abord "
                   "(ses clients reviendront dans une tournée L) ou choisissez un autre livreur.")
    if col2.button("Attribuer", key=f"btn_donner_{cle}", type="primary", width="stretch",
                   disabled=not choix or choix["identifiant"] in occupes):
        res = api("POST", f"/admin/tournees/{t['id']}/attribuer", json={"livreur_id": choix["id"]})
        if res:
            annoncer("success", f"Tournée du {jour:%d/%m/%Y} attribuée à {res['livreur_nom']}. Elle apparaît "
                                "dans son application ; il doit l'optimiser (l'ordre précédent a été effacé).")


POINTS = {"depot": "Dépôt", "domicile": "Domicile"}


def journees_de_trajets(jour: date, livreur_nom: str | None = None) -> None:
    """Lecture seule : étapes saisies par les livreurs qui n'ont pas de tournée ce jour-là."""
    journees = [j for j in api("GET", "/admin/trajets", params={"date": jour.isoformat()}) or []
                if livreur_nom is None or j["livreur_nom"] == livreur_nom]
    if not journees:
        return
    st.subheader(f"Journée de trajets du {jour:%d/%m/%Y}")
    st.caption("Étapes saisies par le livreur lui-même (garage, contrôle technique…). Consultation seule.")
    for j in journees:
        resume = (f"{j['livreur_nom']} — {len(j['etapes'])} étape(s), {j['nb_faites']} faite(s) — "
                  f"{POINTS.get(j['depart'], j['depart'])} {j['heure_depart'][:5]} → "
                  f"{POINTS.get(j['retour'], j['retour'])}")
        if j["optimisee"] and j["distance_km"] is not None:
            resume += f" — {nombre_fr(j['distance_km'])} km"
        with st.expander(resume):
            st.dataframe(pd.DataFrame([{
                "N°": e["ordre"], "Étape": e["libelle"], "Adresse": e["adresse"],
                "Rendez-vous": f"{e['rdv_debut'][:5]}–{e['rdv_fin'][:5]}" if e["rdv_debut"] else "",
                "Sur place (min)": e["duree_min"], "Prévu": outils.heure_locale(e["heure_prevue"]),
                "Fait à": outils.heure_locale(e["faite_le"])} for e in j["etapes"]]),
                hide_index=True, width="stretch")


def suivi_tournee(resume: dict) -> None:
    t = api("GET", f"/admin/tournees/{resume['id']}")
    if t is None:
        return
    if t.get("depart_depot_au_plus_tard"):
        st.info(f"Départ du dépôt au plus tard : **{t['depart_depot_au_plus_tard'][:5]}** "
                f"(période {t['heure_depart'][:5]}–{t['heure_fin'][:5]}, dans l'ordre affiché).")
    outils.carte_tournee(t)
    st.dataframe(pd.DataFrame([{
        "N°": l["ordre_passage"], "Client": l["client"]["nom"], "Ville": l["client"]["ville"],
        "Créneau": f"{(l['client']['creneau_debut'] or '')[:5]}–{(l['client']['creneau_fin'] or '')[:5]}",
        "Prévu": outils.heure_locale(l["heure_arrivee_prevue"]),
        "Statut": LIBELLES_STATUT.get(l["statut"], l["statut"]),
        "Arrivée": outils.heure_locale(l["heure_debut_livraison"]),
        "Sur place (min)": round(l["temps_service_reel_s"] / 60) if l["temps_service_reel_s"] else None,
        "Prédit (min)": round(l["temps_service_predit_s"] / 60) if l["temps_service_predit_s"] else None,
    } for l in t["livraisons"]]), hide_index=True, width="stretch")
    modifiable = resume["statut"] == "planifiee" and date.fromisoformat(resume["date_tournee"]) >= date.today()
    if modifiable:
        with st.container(border=True):
            st.markdown("**Livreur de la tournée** : " + (resume["livreur_nom"] or "—"))
            donner_a_un_livreur(resume, f"suivi_{resume['id']}", sauf=resume["livreur"])
            if resume["livreur"]:
                confirme = st.checkbox("Je confirme le retrait du livreur", key=f"confirme_retrait_{resume['id']}")
                if st.button("Annuler l'attribution (les clients reviennent dans une tournée L)", disabled=not confirme,
                             key=f"retrait_{resume['id']}", width="stretch"):
                    l = api("POST", f"/admin/tournees/{resume['id']}/desattribuer")
                    if l:
                        st.session_state.pop("planning_detail", None)
                        annoncer("success", f"Attribution annulée : la tournée n'apparaît plus chez "
                                            f"{resume['livreur_nom']}. Ses {l['nb_clients']} clients sont dans la "
                                            f"tournée {l['code']}, à attribuer (rubrique Tournées).")
    elif resume["statut"] != "terminee":
        st.caption("Tournée commencée : son livreur ne peut plus être changé.")
    if resume["statut"] != "terminee" and resume["livreur"]:
        with st.form(f"ajout_existant_{resume['id']}", clear_on_submit=True):
            st.write("Ajouter un client déjà enregistré à cette tournée")
            ident = st.text_input("Identifiant du client (10 chiffres)", max_chars=10)
            if st.form_submit_button("Ajouter"):
                if api("POST", f"/admin/tournees/{resume['id']}/clients", attendu=(201,),
                       json={"id_client": ident.strip()}):
                    annoncer("success", "Client ajouté : le livreur doit relancer l'optimisation.")


# --------------------------------------------------------------------------- planning des livreurs
JOURS_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
COULEURS_STATUT = {"planifiee": "blue", "en_cours": "orange", "terminee": "green"}
ICONES_STATUT = {"planifiee": ":material/schedule:", "en_cours": ":material/local_shipping:",
                 "terminee": ":material/check_circle:"}
PLAGES = {(7, 14): "Matin", (13, 19): "Après-midi", (17, 22): "Soirée", (7, 22): "Journée"}


def plage_texte(debut: str | None, fin: str | None) -> str:
    if not debut or not fin:
        return "Journée"
    d, f = int(debut[:2]), int(fin[:2])
    return PLAGES.get((d, f), f"{d} h–{f} h")


def onglet_planning() -> None:
    aujourdhui = date.today()
    st.session_state.setdefault("planning_debut", aujourdhui - timedelta(days=aujourdhui.weekday()))
    debut = st.session_state.planning_debut
    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    if c1.button("◀ Semaine précédente", width="stretch"):
        st.session_state.planning_debut = debut - timedelta(days=7)
        st.rerun()
    if c2.button("Cette semaine", width="stretch"):
        st.session_state.planning_debut = aujourdhui - timedelta(days=aujourdhui.weekday())
        st.rerun()
    if c3.button("Semaine suivante ▶", width="stretch"):
        st.session_state.planning_debut = debut + timedelta(days=7)
        st.rerun()
    c4.markdown(f"#### Semaine du {debut:%d/%m} au {debut + timedelta(days=6):%d/%m/%Y}")
    p = api("GET", "/admin/planning", params={"debut": debut.isoformat()})
    if p is None:
        return

    # ---- tournées L à attribuer : choisir une tournée puis cliquer « Attribuer » dans la grille
    bac = {t["id"]: t for t in p["a_attribuer"]}
    with st.container(border=True):
        st.markdown(f"**Tournées à attribuer ({len(bac)})**")
        if not bac:
            st.caption("Aucune tournée à attribuer : importez un fichier de clients dans la rubrique « Tournées ».")
            choix = None
        else:
            cle = st.selectbox("Tournée à attribuer", [None] + list(bac), key="planning_choix",
                               placeholder="Choisir une tournée L",
                               format_func=lambda i: "— aucune (consulter le planning) —" if i is None else titre_l(bac[i]))
            choix = bac.get(cle)
            if choix:
                st.info(f"Cliquez sur **« Attribuer »** le jour voulu, sur la ligne d'un livreur libre (camion "
                        f"{choix['type_camion']}). Période {LIBELLES_PERIODE[choix['periode']].lower()}, départ "
                        "au début de la période : pour les modifier, passez par la rubrique « Tournées ».")

    # ---- grille livreurs x jours
    jours = [date.fromisoformat(j) for j in p["jours"]]
    cases = {(c["livreur_id"], c["date"]): c for c in p["cases"]}
    largeurs = [2] + [1] * len(jours)
    entete = st.columns(largeurs)
    entete[0].markdown("**Livreur**")
    for col, j in zip(entete[1:], jours):
        col.markdown(f"**{JOURS_COURTS[j.weekday()]} {j:%d/%m}**" + (" · aujourd'hui" if j == aujourdhui else ""))
    if not p["livreurs"]:
        st.info("Aucun livreur actif.")
    for l in p["livreurs"]:
        ligne = st.columns(largeurs)
        ligne[0].markdown(f"**{l['nom']}**  \n:gray[{l['vehicule'] or 'sans camion'}"
                          f"{' · ' + l['type_camion'] if l['type_camion'] else ''}]")
        for col, j in zip(ligne[1:], jours):
            case = cases.get((l["id"], j.isoformat()))
            cle = f"pl_{l['id']}_{j.isoformat()}"
            if case and case["nature"] == "tournee":
                couleur = COULEURS_STATUT.get(case["statut"], "gray")
                texte = f":{couleur}[{plage_texte(case['heure_depart'], case['heure_fin'])} · {case['nb_clients']} cl.]"
                if col.button(texte, key=cle, width="stretch", icon=ICONES_STATUT.get(case["statut"]),
                              help=f"{STATUTS_TOURNEE.get(case['statut'], case['statut'])} · {case['zone'] or ''} · "
                                   f"{case['nb_terminees']} pointé(s) · cliquer pour voir ou modifier"):
                    st.session_state.planning_detail = {**case, "livreur": l["identifiant"], "livreur_nom": l["nom"]}
                    st.session_state.pop("planning_trajets", None)
                    st.rerun()
                if case["nb_etapes"] and col.button(f":violet[+ trajets · {case['nb_etapes']}]", key=cle + "_t",
                                                    width="stretch", icon=":material/route:",
                                                    help="Trajets saisis par le livreur en plus de sa tournée."):
                    st.session_state.planning_trajets = (j.isoformat(), l["nom"])
                    st.session_state.pop("planning_detail", None)
                    st.rerun()
            elif case:
                if col.button(f":violet[Trajets · {case['nb_etapes']}]", key=cle, width="stretch",
                              icon=":material/route:", help="Journée de trajets saisie par le livreur : voir les étapes."):
                    st.session_state.planning_trajets = (j.isoformat(), l["nom"])
                    st.session_state.pop("planning_detail", None)
                    st.rerun()
            elif choix and j >= aujourdhui:
                if l["type_camion"] == choix["type_camion"]:
                    if col.button("Attribuer", key=cle, type="primary", width="stretch", icon=":material/add:",
                                  help=f"{choix['code']} le {j:%d/%m} à {l['nom']}"):
                        attribuer_l(choix, l["id"], l["nom"], j, choix["periode"], None)
                else:
                    col.button(":gray[camion ≠]", key=cle, width="stretch", disabled=True,
                               help=f"{choix['code']} exige un camion {choix['type_camion']}.")
            else:
                col.markdown(":gray[—]" if j < aujourdhui else ":gray[libre]")
    st.caption("Tournée prévue (horloge), en cours (camion), terminée (coche) ; journée de trajets (itinéraire). "
               "Cliquez sur une tournée pour la voir, changer de livreur ou annuler l'attribution.")

    # ---- détail d'une tournée cliquée (oublié si la tournée a changé de livreur entre-temps)
    detail = st.session_state.get("planning_detail")
    if detail and cases.get((detail["livreur_id"], detail["date"]), {}).get("tournee_id") != detail["tournee_id"]:
        st.session_state.pop("planning_detail", None)
        detail = None
    if detail:
        st.divider()
        j = date.fromisoformat(detail["date"])
        c1, c2 = st.columns([5, 1])
        c1.subheader(f"{detail['livreur_nom']} — {JOURS_SEMAINE[j.weekday()]} {j:%d/%m/%Y} · "
                     f"{plage_texte(detail['heure_depart'], detail['heure_fin'])} · {detail['nb_clients']} clients")
        if c2.button("Fermer", width="stretch"):
            st.session_state.pop("planning_detail", None)
            st.rerun()
        suivi_tournee({"id": detail["tournee_id"], "statut": detail["statut"], "date_tournee": detail["date"],
                       "livreur": detail["livreur"], "livreur_nom": detail["livreur_nom"]})
    trajets = st.session_state.get("planning_trajets")
    if trajets:
        st.divider()
        c1, c2 = st.columns([5, 1])
        if c2.button("Fermer", key="fermer_trajets", width="stretch"):
            st.session_state.pop("planning_trajets", None)
            st.rerun()
        with c1:
            journees_de_trajets(date.fromisoformat(trajets[0]), trajets[1])


# --------------------------------------------------------------------------- tournées L (import -> attribution)
def titre_l(t: dict) -> str:
    cps = t["codes_postaux"]
    depart = (t["depart_au_plus_tard"] or "")[:5]
    return (f"{t['code']} · {LIBELLES_PERIODE[t['periode']]} · {t['zone'] or 'zone non définie'} · "
            f"{t['nb_clients']} clients · CP {', '.join(cps[:6])}{'…' if len(cps) > 6 else ''} · camion {t['type_camion']}"
            + (f" · départ avant {depart}" if depart else "")
            + (f" · :red[limite de {t['limite']} dépassée : à alléger avant d'attribuer]"
               if t["nb_clients"] > t["limite"] else ""))


def onglet_tournees() -> None:
    importer_clients()
    tournees = api("GET", "/admin/tournees-l")
    if tournees is None:
        return
    st.subheader(f"Tournées à attribuer ({len(tournees)})")
    if not tournees:
        st.info("Aucune tournée à attribuer : importez un fichier de clients ci-dessus.")
        return
    c1, c2, c3 = st.columns(3)
    f_periode = c1.multiselect("Période", list(LIBELLES_PERIODE), format_func=LIBELLES_PERIODE.get,
                               placeholder="Toutes", key="filtre_periode")
    f_camion = c2.multiselect("Camion", ["normal", "hayon"], placeholder="Tous", key="filtre_camion")
    f_cp = c3.text_input("Code postal", placeholder="ex. 69100", key="filtre_cp").strip()
    visibles = [t for t in tournees if (not f_periode or t["periode"] in f_periode)
                and (not f_camion or t["type_camion"] in f_camion)
                and (not f_cp or any(cp.startswith(f_cp) for cp in t["codes_postaux"]))]
    st.caption("Ouvrez une tournée pour la visualiser. Choisissez la date et le livreur (et si besoin la période, "
               "l'heure de départ ou les clients), puis « Attribuer ». Vous pouvez aussi attribuer depuis le Planning.")
    toutes = {t["id"]: t for t in tournees}
    livreurs = livreurs_actifs()
    occupations: dict[date, dict] = {}
    for t in visibles:
        with st.expander(titre_l(t)):
            detail_l(t, toutes, livreurs, occupations)


def detail_l(t: dict, toutes: dict[int, dict], livreurs: list[dict], occupations: dict[date, dict]) -> None:
    i = t["id"]
    c1, c2, c3 = st.columns(3)
    jour = c1.date_input("Date de la tournée", value=date.today() + timedelta(days=1), min_value=date.today(),
                         format="DD/MM/YYYY", key=f"l_date_{i}")
    choix_periodes = list(LIBELLES_PERIODE)
    periode = c2.selectbox("Période", choix_periodes, index=choix_periodes.index(t["periode"]),
                           format_func=LIBELLES_PERIODE.get, key=f"l_per_{i}")
    v = t if periode == t["periode"] else (api("GET", f"/admin/tournees-l/{i}", params={"periode": periode}) or t)
    debut = datetime.strptime(v["debut_periode"][:5], "%H:%M").time()
    conseil = (v["depart_au_plus_tard"] or "")[:5]
    depart = c3.time_input("Départ du dépôt", value=debut, step=900, key=f"l_dep_{i}_{periode}",
                           help="Heure à laquelle le livreur quitte le dépôt. Par défaut : début de la période."
                                + (f" Au plus tard {conseil} pour respecter tous les créneaux." if conseil else ""))

    charge = " · ".join(f"{h} h : {n}" for h, n in v["charge_par_heure"].items())
    st.caption(f"**{v['nb_clients']} / {v['limite']} clients** · clients prévus par heure : {charge}"
               + (f" · départ du dépôt conseillé au plus tard à **{conseil}**" if conseil else "")
               + ". Ordre horaire ; le livreur optimisera l'ordre géographique.")
    if v["nb_clients"] > v["limite"]:
        st.error(f"{v['nb_clients']} clients pour une limite de {v['limite']} sur cette période : retirez-en ou "
                 "déplacez-en vers une autre tournée, ou choisissez la journée complète.")
    if v["refuses"]:
        st.warning(f"{len(v['refuses'])} client(s) au-delà de 2 par heure sur leur créneau : risque de retard.")
    if conseil and depart.strftime("%H:%M") > conseil:
        st.warning(f"Départ à {depart:%H:%M}, après l'heure conseillée ({conseil}) : des créneaux risquent d'être manqués.")

    # Clients : garder, retirer, ou déplacer vers une autre tournée L (même camion)
    cibles = {f"Déplacer vers {o['code']}": o["id"] for o in toutes.values()
              if o["id"] != i and o["type_camion"] == t["type_camion"]}
    df = pd.DataFrame([{
        "Action": "Garder",
        "Passage visé": f"{c['heure_visee']} h – {c['heure_visee'] + 1} h" if c["heure_visee"] is not None else "à revoir",
        "Créneau": f"{(c['creneau_debut'] or '')[:5]}–{(c['creneau_fin'] or '')[:5]}",
        "Client": c["nom"], "Code postal": c["code_postal"], "Ville": c["ville"].title(),
        "Identifiant": c["id_client"]} for c in v["clients"]])
    edite = st.data_editor(
        df, hide_index=True, width="stretch", key=f"l_tab_{i}_{periode}_{st.session_state.get(f'l_v_{i}', 0)}",
        disabled=["Passage visé", "Créneau", "Client", "Code postal", "Ville", "Identifiant"],
        column_config={"Action": st.column_config.SelectboxColumn(
            "Action", options=["Garder", "Retirer"] + list(cibles), required=True,
            help="Retirer le client de la tournée, ou le déplacer vers une autre tournée L.")})
    changements = [(l["Identifiant"], l["Action"]) for _, l in edite.iterrows() if l["Action"] != "Garder"]
    if changements:
        st.session_state[f"l_v_{i}"] = st.session_state.get(f"l_v_{i}", 0) + 1     # tableau neuf au rechargement
        messages = []
        for id_client, action in changements:
            ok, res = api_brut("POST", f"/admin/tournees-l/{i}/clients/{id_client}/deplacer",
                               json={"vers": cibles.get(action)})
            messages.append(f"{id_client} : {'retiré' if action == 'Retirer' else action.lower()}" if ok
                            else f"{id_client} : {res}")
        annoncer("info", f"{t['code']} modifiée — " + " ; ".join(messages))

    # Livreur et attribution
    occupes = occupations.get(jour)
    if occupes is None:
        occupes = occupations[jour] = occupation(jour)
    candidats = sorted((c for c in livreurs if c["type_camion"] == t["type_camion"]),
                       key=lambda c: (c["identifiant"] in occupes, c["nom"]))
    if not candidats:
        st.warning(f"Aucun livreur actif avec un camion {t['type_camion']}.")
        return
    livreur = st.selectbox("Livreur", candidats, index=None, placeholder="Choisir un livreur", key=f"l_liv_{i}",
                           format_func=lambda c: libelle_livreur(c, occupes, jour))
    pris = bool(livreur) and livreur["identifiant"] in occupes
    if pris:
        st.warning(f"{livreur['prenom']} {livreur['nom']} a déjà une tournée le {jour:%d/%m/%Y} : choisissez un "
                   "autre livreur ou une autre date.")
    nom = f"{livreur['prenom']} {livreur['nom']}" if livreur else None
    c1, c2 = st.columns([3, 1])
    if c1.button(f"Attribuer {t['code']}" + (f" à {nom} le {jour:%d/%m/%Y}" if nom else ""), type="primary",
                 width="stretch", key=f"l_attr_{i}", disabled=not livreur or pris or v["nb_clients"] > v["limite"]):
        attribuer_l(t, livreur["id"], nom, jour, periode, depart if depart != debut else None)
    confirme = c2.checkbox("Confirmer la suppression", key=f"l_conf_{i}")
    if c2.button(f"Supprimer {t['code']}", key=f"l_suppr_{i}", disabled=not confirme, width="stretch"):
        ok, res = api_brut("DELETE", f"/admin/tournees-l/{i}")
        if ok:
            annoncer("success", f"Tournée {t['code']} supprimée : ses clients ne sont plus planifiés.")
        st.error(str(res))


def attribuer_l(t: dict, livreur_id: int, nom: str, jour: date, periode: str, heure_depart) -> None:
    ok, res = api_brut("POST", f"/admin/tournees-l/{t['id']}/attribuer", json={
        "date_tournee": jour.isoformat(), "livreur_id": livreur_id, "periode": periode,
        "heure_depart": heure_depart.strftime("%H:%M") if heure_depart else None})
    if not ok:
        st.error(f"{t['code']} non attribuée : {res}")
        return
    st.session_state.pop("planning_choix", None)
    texte = (f"{t['code']} attribuée à {nom} le {jour:%d/%m/%Y} ({res['nb_clients']} clients, départ "
             f"{res['heure_depart'][:5]}). Elle apparaît dans son application et dans le Planning.")
    if res.get("avertissements"):
        annoncer("warning", texte + " À vérifier : " + " ".join(res["avertissements"]))
    annoncer("success", texte)


def importer_clients() -> None:
    bilan = st.session_state.get("bilan_import")
    with st.expander("Importer un fichier de clients (CSV)", expanded=bilan is None):
        st.caption("Format de l'échantillon **tournees_esis.csv** (séparateur « , » ou « ; ») : `id_client`, "
                   "`nom_client`, `adresse`, `creneau_horaire_prefere` obligatoires ; `telephone`, `digicode`, "
                   "`notes_livraison` facultatifs. Un client présent plusieurs fois n'est livré qu'une fois.")
        fichier = st.file_uploader("Fichier CSV", type=["csv"], key="fichier_csv")
        c1, c2 = st.columns(2)
        journee = c1.toggle("Tournées à la journée complète", key="import_journee",
                            help="Par défaut, les clients sont répartis en matin, après-midi et soirée selon leur "
                                 "créneau ; 20 clients au plus seulement sur une journée complète.")
        max_geo = c2.slider("Nouvelles adresses à géocoder tout de suite", 0, 60, 15, key="import_geo",
                            help="Nominatim : 1 adresse par seconde. Les autres peuvent être géocodées ensuite.")
        if fichier is not None and st.button("Importer et préparer les tournées", type="primary"):
            if fichier.size > 2_000_000:
                st.error("Fichier trop volumineux (2 Mo maximum).")
                return
            contenu = lire_fichier(fichier)
            if contenu is None:
                return
            with st.spinner("Import, nettoyage, géocodage des nouvelles adresses et préparation des tournées..."):
                resultat = api("POST", "/admin/import-csv", timeout=180, json={
                    "nom_fichier": fichier.name[:100], "contenu": contenu, "max_geocodages": max_geo})
                generation = None
                if resultat and resultat["ids_valides"]:
                    generation = api("POST", "/admin/tournees-l/generer", attendu=(201,), timeout=60, json={
                        "clients": resultat["ids_valides"], "journee_continue": journee})
            if resultat:
                st.session_state.bilan_import = {"import": resultat, "generation": generation, "journee": journee}
                st.rerun()
    if bilan:
        bilan_import(bilan)


def bilan_import(b: dict) -> None:
    imp, gen = b["import"], b["generation"] or {"tournees": [], "exclus": []}
    with st.container(border=True):
        codes = [t["code"] for t in gen["tournees"]]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Lignes lues", imp["lignes"])
        c2.metric("Clients importés", len(set(imp["ids_valides"])))
        c3.metric("Tournées préparées", len(codes), help=", ".join(codes) or None)
        c4.metric("Clients écartés", len(gen["exclus"]) + len(imp["rejets"]))
        if codes:
            st.success(f"Tournées préparées : {codes[0]} à {codes[-1]}. Elles sont listées ci-dessous.")
        if imp["rejets"]:
            with st.expander(f"{len(imp['rejets'])} ligne(s) rejetée(s) à l'import"):
                st.dataframe(pd.DataFrame(imp["rejets"]), hide_index=True, width="stretch")
        if gen["exclus"]:
            with st.expander(f"{len(gen['exclus'])} client(s) non placé(s) dans une tournée"):
                st.dataframe(pd.DataFrame(gen["exclus"]).rename(columns={"id_client": "Client", "motif": "Motif"}),
                             hide_index=True, width="stretch")
        a_geocoder = imp["geocodage"]["restants"]
        c1, c2 = st.columns([3, 1])
        if a_geocoder and c1.button(f"Géocoder 20 adresses en attente ({a_geocoder}) et préparer leurs tournées"):
            with st.spinner("Géocodage (1 adresse par seconde)..."):
                res = api("POST", "/admin/clients/geocoder", timeout=180, json={"maximum": 20})
                nouvelle = api("POST", "/admin/tournees-l/generer", attendu=(201,), timeout=60, json={
                    "clients": imp["ids_valides"], "journee_continue": b["journee"]})
            if res and nouvelle:
                imp["geocodage"]["restants"] = res["restants"]
                gen["tournees"] += nouvelle["tournees"]
                gen["exclus"] = [e for e in nouvelle["exclus"] if not e["motif"].startswith("déjà dans la tournée")]
                st.session_state.bilan_import = {"import": imp, "generation": gen, "journee": b["journee"]}
                st.rerun()
        if c2.button("Fermer le bilan", width="stretch"):
            st.session_state.pop("bilan_import", None)
            st.rerun()


def onglet_pilotage() -> None:
    tableau, securite = st.tabs(["Tableau de bord", "Sécurité"])
    with tableau:
        onglet_tableau_de_bord()
    with securite:
        onglet_securite()


# --------------------------------------------------------------------------- import CSV
def lire_fichier(fichier) -> str | None:
    brut = fichier.getvalue()
    for encodage in ("utf-8-sig", "cp1252"):        # UTF-8, puis exports Excel Windows
        try:
            return brut.decode(encodage)
        except UnicodeDecodeError:
            continue
    st.error("Encodage du fichier non reconnu (UTF-8 ou Windows attendu).")
    return None


def api_brut(methode: str, chemin: str, **kwargs) -> tuple[bool, dict | str]:
    """Comme api(), mais renvoie l'erreur au lieu de l'afficher (bilan groupé de plusieurs appels)."""
    r = outils.appel_api(methode, chemin, st.session_state.jeton, timeout=30, **kwargs)
    if r is None:
        return False, "Le serveur ne répond pas."
    if r.status_code == 401:
        outils.reinitialiser("Votre session a expiré. Veuillez vous reconnecter.")
        st.rerun()
    try:
        corps = r.json()
    except ValueError:
        corps = {}
    if r.status_code in (200, 201):
        return True, corps
    detail = corps.get("detail") if isinstance(corps, dict) else None
    return False, detail if isinstance(detail, str) else f"Erreur {r.status_code}."


# --------------------------------------------------------------------------- livreurs
def retenir_code(identifiant: str, res: dict) -> None:
    """Le code reste affiché jusqu'à ce que le planificateur confirme l'avoir remis."""
    if res.get("code_activation"):
        st.session_state.code_a_transmettre = (identifiant, res["code_activation"], res["code_expire_le"])


def encadre_code() -> None:
    identifiant, code, expire = st.session_state.code_a_transmettre
    with st.container(border=True):
        st.markdown(f"#### Code d'activation de **{identifiant}**")
        st.code(code, language=None)
        st.write(f"Valable jusqu'au **{date_heure(expire)}**, utilisable une seule fois. À remettre au salarié "
                 "par un canal sûr : en main propre ou par téléphone, jamais par e-mail.")
        st.caption("Première connexion (application livreur ou interface web) : identifiant + ce code, puis "
                   "choix de son mot de passe (saisi deux fois), puis activation de la double authentification "
                   "par QR code, puis lecture des conditions d'utilisation. Vous ne connaîtrez jamais son mot de "
                   "passe. Ce code ne sera plus affiché : en cas de perte, « Réinitialiser le mot de passe » en "
                   "génère un nouveau.")
        if st.button("J'ai remis le code au salarié", type="primary"):
            del st.session_state["code_a_transmettre"]
            st.rerun()


def etat_activation(c: dict) -> str:
    if not c["activation_en_attente"]:
        return "faite" if c["derniere_connexion"] or c["totp_active"] else "—"
    expire = datetime.fromisoformat(c["activation_expire_le"])
    return "code expiré" if expire <= datetime.now(timezone.utc) else f"en attente (→ {date_heure(c['activation_expire_le'])})"


def onglet_livreurs() -> None:
    if st.session_state.get("code_a_transmettre"):
        encadre_code()
    comptes = api("GET", "/admin/utilisateurs") or []
    vehicules = api("GET", "/admin/vehicules") or []
    st.dataframe(pd.DataFrame([{
        "Identifiant": c["identifiant"], "Nom": f"{c['prenom']} {c['nom']}", "Rôle": c["role"],
        "Type": c["type_livreur"] or "—",
        "Domicile": ("renseigné" if c["domicile_renseigne"] else "à renseigner par le livreur")
        if c["type_livreur"] == "interne" else "—",
        "Actif": c["actif"], "Activation": etat_activation(c), "2FA": c["totp_active"],
        "Verrouillé": c["verrouille"],
        "Camion": f"{c['vehicule']} ({c['type_camion']})" if c["vehicule"] else "—",
        "Dernière connexion": date_heure(c["derniere_connexion"]),
        "Conditions": (f"v{c['conditions_version']} le {date_fr(c['conditions_acceptees_le'])}"
                       if c["conditions_acceptees_le"] else "non acceptées" if c["role"] == "livreur" else "—")}
        for c in comptes]),
        hide_index=True, width="stretch")

    col1, col2 = st.columns(2)
    with col1, st.form("creer_compte", clear_on_submit=True):
        st.subheader("Nouveau compte")
        identifiant = st.text_input("Identifiant", placeholder="prenom.nom", max_chars=50)
        prenom = st.text_input("Prénom", max_chars=80)
        nom = st.text_input("Nom", max_chars=80)
        role = st.radio("Rôle", ["livreur", "admin"], horizontal=True)
        type_livreur = st.radio("Type de livreur", list(TYPES_LIVREUR), horizontal=True, index=None,
                                help="Interne : rentre chez lui avec le camion (départ et retour à son "
                                     "domicile, qu'il renseigne lui-même). Prestataire : rend le camion "
                                     "à l'entrepôt (départ et retour au dépôt).")
        libres = [None] + [v for v in vehicules if v["actif"]]
        vehicule = st.selectbox("Camion (livreur)", libres, format_func=lambda v: "—" if v is None else
                                f"{v['immatriculation']} ({v['type_camion']})" +
                                (f" — utilisé par {v['utilise_par']}" if v["utilise_par"] else ""))
        if st.form_submit_button("Créer le compte", type="primary"):
            res = None
            if role == "livreur" and type_livreur is None:
                st.error("Choisissez « Livreur interne » ou « Livreur prestataire ».")
            else:
                res = api("POST", "/admin/utilisateurs", attendu=(201,), json={
                    "identifiant": identifiant.strip().lower(), "nom": nom, "prenom": prenom, "role": role,
                    "vehicule_id": vehicule["id"] if vehicule and role == "livreur" else None,
                    "type_livreur": TYPES_LIVREUR[type_livreur] if role == "livreur" else None})
            if res:
                retenir_code(res["compte"]["identifiant"], res)
                st.rerun()

    with col2:
        st.subheader("Gérer un compte")
        compte = st.selectbox("Compte", comptes, index=None, placeholder="Choisir",
                              format_func=lambda c: f"{c['identifiant']} ({c['prenom']} {c['nom']})")
        if compte:
            gerer_compte(compte, vehicules)


def gerer_compte(c: dict, vehicules: list[dict]) -> None:
    def action(nom: str, message: str):
        res = api("POST", f"/admin/utilisateurs/{c['id']}/{nom}")
        if res:
            retenir_code(c["identifiant"], res)
            annoncer("success", message)

    if c["verrouille"] and st.button("Déverrouiller", width="stretch"):
        action("deverrouiller", f"{c['identifiant']} déverrouillé.")
    confirme = st.checkbox("Je confirme l'action sensible ci-dessous", key=f"confirme_{c['id']}")
    if c["actif"]:
        if st.button("Désactiver le compte", disabled=not confirme, width="stretch"):
            action("desactiver", f"{c['identifiant']} désactivé : ses sessions sont coupées.")
    elif st.button("Réactiver le compte", width="stretch"):
        action("reactiver", f"{c['identifiant']} réactivé.")
    if st.button("Réinitialiser le mot de passe", disabled=not confirme, width="stretch"):
        action("reinitialiser-mdp", f"Mot de passe de {c['identifiant']} réinitialisé : remettez-lui le "
                                    "nouveau code d'activation affiché en haut de la page.")
    if st.button("Réinitialiser la double authentification (téléphone perdu)", disabled=not confirme,
                 width="stretch"):
        action("reinitialiser-2fa", f"2FA de {c['identifiant']} réinitialisée : nouveau QR code à la "
                                    "prochaine connexion.")
    if c["role"] == "livreur":
        libelles = list(TYPES_LIVREUR)
        actuel_type = next((i for i, v in enumerate(TYPES_LIVREUR.values()) if v == c["type_livreur"]), 1)
        choix = st.radio("Type de livreur", libelles, index=actuel_type, horizontal=True,
                         key=f"type_livreur_{c['id']}")
        if TYPES_LIVREUR[choix] != c["type_livreur"] and st.button("Enregistrer le type", width="stretch"):
            if api("POST", f"/admin/utilisateurs/{c['id']}/type-livreur",
                   json={"type_livreur": TYPES_LIVREUR[choix]}):
                annoncer("success", f"{c['identifiant']} : {choix.lower()}." + (
                    " Son adresse de domicile a été effacée." if TYPES_LIVREUR[choix] == "prestataire"
                    and c["domicile_renseigne"] else ""))
        options = [None] + [v for v in vehicules if v["actif"]]
        actuel = next((i for i, v in enumerate(options) if v and v["id"] == c["vehicule_id"]), 0)
        vehicule = st.selectbox("Camion attribué", options, index=actuel, key=f"vehicule_{c['id']}",
                                format_func=lambda v: "aucun" if v is None else
                                f"{v['immatriculation']} ({v['type_camion']})")
        if st.button("Affecter ce camion", width="stretch"):
            if api("POST", f"/admin/utilisateurs/{c['id']}/vehicule",
                   json={"vehicule_id": vehicule["id"] if vehicule else None}):
                annoncer("success", f"Camion de {c['identifiant']} mis à jour.")


# --------------------------------------------------------------------------- clients
def onglet_clients() -> None:
    a_corriger = api("GET", "/admin/clients", params={"a_corriger": True, "limite": 100}) or []
    st.subheader(f"Adresses à vérifier ({len(a_corriger)})")
    if a_corriger:
        st.caption("Position approximative (centre de la commune) ou introuvable : placez le client "
                   "à la main en cliquant sur la carte.")
        client = st.selectbox("Client à repositionner", a_corriger, format_func=lambda c: (
            f"{c['id_client']} — {c['adresse']} [{c['precision'] or 'sans position'}]"))
        correction(client)
    else:
        st.success("Tous les clients réels sont localisés précisément.")
    st.divider()
    recherche_et_rgpd()


def correction(c: dict) -> None:
    lat0, lon0 = c["latitude"] or 45.7578, c["longitude"] or 4.8320
    carte = folium.Map(location=[lat0, lon0], zoom_start=15 if c["latitude"] else 11)
    folium.Marker([lat0, lon0], tooltip="Position actuelle",
                  icon=folium.Icon(color="red")).add_to(carte)
    clic = st_folium(carte, height=320, use_container_width=True, returned_objects=["last_clicked"],
                     key=f"carte_correction_{c['id_client']}")
    point = (clic or {}).get("last_clicked")
    with st.form(f"correction_{c['id_client']}"):
        col1, col2 = st.columns(2)
        lat = col1.number_input("Latitude", value=float(point["lat"] if point else lat0), format="%.6f")
        lon = col2.number_input("Longitude", value=float(point["lng"] if point else lon0), format="%.6f")
        camion = st.radio("Livraison", ["normal", "hayon"], horizontal=True,
                          index=0 if c["type_camion_requis"] == "normal" else 1,
                          format_func=lambda t: "Colis (camion normal)" if t == "normal" else "Palette (camion hayon)")
        if st.form_submit_button("Enregistrer la correction", type="primary"):
            if api("POST", f"/admin/clients/{c['id_client']}/correction",
                   json={"latitude": lat, "longitude": lon, "type_camion_requis": camion}):
                annoncer("success", f"Client {c['id_client']} repositionné (précision « manuelle »).")


def recherche_et_rgpd() -> None:
    st.subheader("Rechercher un client")
    texte = st.text_input("Identifiant, nom ou adresse", max_chars=100, key="recherche_client")
    if not texte.strip():
        return
    clients = api("GET", "/admin/clients", params={"recherche": texte.strip(), "limite": 50}) or []
    if not clients:
        st.info("Aucun client trouvé.")
        return
    st.dataframe(pd.DataFrame([{
        "Identifiant": c["id_client"], "Nom": c["nom"], "Adresse": c["adresse"], "Zone": c["zone"],
        "Camion": c["type_camion_requis"], "Précision": c["precision"], "Source": c["source"],
        "Actif": c["actif"]} for c in clients]), hide_index=True, width="stretch")
    client = st.selectbox("Client", clients, index=None, placeholder="Choisir pour exercer un droit RGPD",
                          format_func=lambda c: f"{c['id_client']} — {c['nom']}")
    if not client:
        return
    remarques_client(client)
    st.write("**Droits RGPD** (chaque action est inscrite au journal de sécurité)")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Préparer l'export des données", width="stretch"):
            export = api("GET", f"/admin/clients/{client['id_client']}/export")
            if export:
                st.session_state.export_rgpd = export
        export = st.session_state.get("export_rgpd")
        if export and export["id_client"] == client["id_client"]:
            st.download_button("Télécharger (JSON)", json.dumps(export, ensure_ascii=False, indent=2),
                               file_name=f"export_client_{client['id_client']}.json",
                               mime="application/json", width="stretch")
            st.caption("Contient des données personnelles déchiffrées : à transmettre uniquement à la "
                       "personne concernée, puis à supprimer.")
    with col2:
        if client["actif"]:
            confirmation = st.text_input("Ressaisir l'identifiant pour anonymiser (irréversible)",
                                         max_chars=10, key=f"anonymiser_{client['id_client']}")
            if st.button("Anonymiser le client", disabled=confirmation != client["id_client"],
                         width="stretch"):
                if api("POST", f"/admin/clients/{client['id_client']}/anonymiser",
                       json={"confirmation": confirmation}):
                    st.session_state.pop("export_rgpd", None)
                    annoncer("success", f"Client {client['id_client']} anonymisé.")
        else:
            st.caption("Client inactif ou déjà anonymisé.")


def remarques_client(client: dict) -> None:
    """Remarques des livreurs sur cette adresse : l'administrateur supprime celles qui sont
    inadaptées (avis sur la personne, information excessive : zone de commentaire libre, CNIL)."""
    remarques = api("GET", f"/admin/clients/{client['id_client']}/remarques") or []
    with st.expander(f"Remarques des livreurs sur cette adresse ({len(remarques)})"):
        if not remarques:
            st.caption("Aucune remarque.")
        for r in remarques:
            col1, col2 = st.columns([6, 1], vertical_alignment="center")
            col1.write(f"{date_fr(r['cree_le'])} — {r['texte']}")
            if col2.button("Supprimer", key=f"admin_suppr_remarque_{r['id']}"):
                rep = outils.appel_api("DELETE", f"/admin/remarques/{r['id']}", st.session_state.jeton)
                if rep is not None and rep.status_code == 204:
                    annoncer("success", "Remarque supprimée (action inscrite au journal).")
                st.error("Suppression impossible : rechargez la page.")


# --------------------------------------------------------------------------- sécurité
def onglet_securite() -> None:
    alertes = api("GET", "/admin/journal-securite", params={"limite": 20, "evenement": "CONNEXION_ANORMALE"})
    st.subheader("Connexions anormales détectées par l'IA")
    if alertes:
        st.warning(f"{len(alertes)} alerte(s) récente(s) : vérifiez qu'il s'agit bien des utilisateurs "
                   "concernés. Les connexions ne sont pas bloquées.")
        st.dataframe(pd.DataFrame([{
            "Date": date_heure(a["horodatage"]), "Compte": a["identifiant_saisi"], "IP": a["adresse_ip"],
            "Raisons": ", ".join((a["details"] or {}).get("raisons", [])),
            "Score": (a["details"] or {}).get("score")} for a in alertes]),
            hide_index=True, width="stretch")
    elif alertes is not None:
        st.success("Aucune connexion anormale récente.")

    st.subheader("Journal de sécurité")
    col1, col2 = st.columns([2, 1])
    evenement = col1.selectbox("Type d'événement", EVENEMENTS)
    limite = col2.selectbox("Lignes", [50, 100, 200, 500])
    params = {"limite": limite} | ({} if evenement == "(tous)" else {"evenement": evenement})
    lignes = api("GET", "/admin/journal-securite", params=params) or []
    st.dataframe(pd.DataFrame([{
        "Date": date_heure(e["horodatage"]), "Événement": e["evenement"], "Succès": e["succes"],
        "Compte": e["identifiant_saisi"], "IP": e["adresse_ip"],
        "Détails": ", ".join(f"{k}={v}" for k, v in (e["details"] or {}).items())} for e in lignes]),
        hide_index=True, width="stretch")
    st.caption("Journal en ajout seul : la base refuse toute modification et toute suppression "
               "d'une entrée de moins d'un an.")


# --------------------------------------------------------------------------- point d'entrée
def ecran_admin(profil: dict, outils_communs) -> None:
    global outils
    outils = outils_communs
    st.title("Espace administrateur")
    if message := st.session_state.pop("message_admin", None):
        getattr(st, message[0])(message[1])
    # Onglets mémorisés (st.tabs reviendrait au premier onglet après chaque action)
    onglet = st.segmented_control("Rubrique", ONGLETS, default=ONGLETS[0], key="onglet_admin",
                                  label_visibility="collapsed") or ONGLETS[0]
    {
        "Tournées": onglet_tournees,
        "Planning": onglet_planning,
        "Livreurs": onglet_livreurs,
        "Clients": onglet_clients,
        "Tableau de bord et sécurité": onglet_pilotage,
    }[onglet]()
