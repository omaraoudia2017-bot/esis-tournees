"""Espace administrateur (module 4) : tableau de bord, tournées du jour, import CSV, livreurs,
clients (correction, RGPD), sécurité.

Appelé par app.py, qui fournit les outils communs (appel à l'API, session, carte de tournée).
Toutes les règles (droits, refus, journal) sont appliquées par l'API : cet écran ne fait qu'afficher.
"""
import json
from datetime import date, datetime, timedelta, timezone

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

ONGLETS = ["Tableau de bord", "Planning", "Tournées du jour", "Import CSV", "Répartition", "Livreurs", "Clients",
           "Sécurité"]
STATUTS_TOURNEE = {"planifiee": "Planifiée", "en_cours": "En cours", "terminee": "Terminée"}
LIBELLES_STATUT = {"a_livrer": "À livrer", "en_cours": "En cours", "livre": "Livré", "absent": "Absent",
                   "refuse": "Refusé", "reporte": "À reporter"}
TYPES_LIVREUR = {"Livreur interne": "interne", "Livreur prestataire": "prestataire"}
PERIODES = {"Matin (7 h – 14 h)": "matin", "Après-midi (13 h – 19 h)": "apres-midi",
            "Soirée (17 h – 22 h)": "soiree", "Journée complète": "journee"}
EVENEMENTS = ["(tous)", "CONNEXION_ANORMALE", "CONNEXION_ECHEC", "CONNEXION_REUSSIE", "COMPTE_VERROUILLE",
              "ACCES_REFUSE", "IMPORT_CSV", "ADMIN_CREATION_COMPTE", "ADMIN_DESACTIVER",
              "ADMIN_REINITIALISER_MDP", "ADMIN_REINITIALISER_2FA", "ADMIN_ATTRIBUTION_TOURNEE",
              "ADMIN_CORRECTION_CLIENT", "RGPD_EXPORT", "RGPD_ANONYMISATION", "AJOUT_CLIENT_VOLEE",
              "CONSULTATION_TOURNEE", "POINTAGE_ANNULE", "REMARQUE_AJOUTEE", "REMARQUE_SUPPRIMEE",
              "CONDITIONS_ACCEPTEES", "ADMIN_TYPE_LIVREUR", "DOMICILE_MODIFIE", "DOMICILE_SUPPRIME",
              "TRAJET_ETAPE_AJOUTEE", "TRAJET_ETAPE_SUPPRIMEE", "TRAJETS_OPTIMISES",
              "CODE_ACTIVATION_VALIDE", "MOT_DE_PASSE_DEFINI", "ADMIN_DESATTRIBUTION_TOURNEE",
              "ADMIN_REATTRIBUTION_TOURNEE"]

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


# --------------------------------------------------------------------------- tournées du jour
EN_ATTENTE = "— en attente d'un livreur —"
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
        st.warning(f"{choix['prenom']} {choix['nom']} a déjà une tournée le {jour:%d/%m/%Y} : retirez-la "
                   "d'abord (elle passera en attente) ou choisissez un autre livreur.")
    if col2.button("Attribuer", key=f"btn_donner_{cle}", type="primary", width="stretch",
                   disabled=not choix or choix["identifiant"] in occupes):
        res = api("POST", f"/admin/tournees/{t['id']}/attribuer", json={"livreur_id": choix["id"]})
        if res:
            st.session_state.jour_a_afficher = jour
            annoncer("success", f"Tournée du {jour:%d/%m/%Y} attribuée à {res['livreur_nom']}. Elle apparaît "
                                "dans son application ; il doit l'optimiser (l'ordre précédent a été effacé).")


def tournees_en_attente() -> None:
    attente = api("GET", "/admin/tournees/en-attente") or []
    if not attente:
        return
    with st.container(border=True):
        st.markdown(f"#### Tournées en attente d'un livreur ({len(attente)})")
        st.caption("Attribution annulée : les clients et la date sont conservés. Choisissez un livreur libre ce "
                   "jour-là et équipé du bon camion.")
        for t in attente:
            j = date.fromisoformat(t["date_tournee"])
            st.markdown(f"**{JOURS_SEMAINE[j.weekday()]} {j:%d/%m/%Y}** · {t['zone'] or 'zone non définie'} · "
                        f"{(t['heure_depart'] or '')[:5]}–{(t['heure_fin'] or '')[:5]} · {t['nb_clients']} clients")
            donner_a_un_livreur(t, f"attente_{t['id']}")


def onglet_tournees() -> None:
    tournees_en_attente()
    # Après une attribution : la liste s'ouvre sur le jour de la tournée créée
    if jour_cree := st.session_state.pop("jour_a_afficher", None):
        st.session_state.tournees_jour = jour_cree
    jour = st.date_input("Jour affiché", value=date.today(), format="DD/MM/YYYY", key="tournees_jour")
    tournees = api("GET", "/admin/tournees", params={"date": jour.isoformat()})
    if tournees is None:
        return
    st.subheader(f"Tournées du {jour:%d/%m/%Y} ({len(tournees)})")
    if tournees:
        st.dataframe(pd.DataFrame([{
            "Livreur": t["livreur_nom"] or EN_ATTENTE,
            "Camion": f"{t['vehicule']} ({t['type_camion']})" if t["vehicule"] else "—",
            "Zone": t["zone"], "Période": f"{(t['heure_depart'] or '')[:5]}–{(t['heure_fin'] or '')[:5]}",
            "Statut": STATUTS_TOURNEE.get(t["statut"], t["statut"]),
            "Clients": f"{t['nb_clients']} / {t['max_clients']}", "Pointés": t["nb_terminees"],
            "À reporter": t["nb_a_reporter"], "Optimisée": "oui" if t["optimisee"] else "non",
            "km": nombre_fr(t["distance_totale_km"]) if t["distance_totale_km"] is not None else "—",
            "Attribuée le": date_heure(t.get("attribuee_le"))}
            for t in tournees]), hide_index=True, width="stretch")
        choix = st.selectbox("Suivre ou modifier une tournée", tournees, index=None, placeholder="Choisir un livreur",
                             format_func=lambda t: f"{t['livreur_nom'] or EN_ATTENTE} — {t['nb_clients']} clients")
        if choix:
            suivi_tournee(choix)
    else:
        st.info(f"Aucune tournée le {jour:%d/%m/%Y}.")
    journees_de_trajets(jour)
    st.divider()
    formulaire_tournee(jour)

POINTS = {"depot": "Dépôt", "domicile": "Domicile"}


def journees_de_trajets(jour: date) -> None:
    """Lecture seule : étapes saisies par les livreurs qui n'ont pas de tournée ce jour-là."""
    journees = api("GET", "/admin/trajets", params={"date": jour.isoformat()})
    if not journees:
        return
    st.subheader(f"Journées de trajets, sans tournée ({len(journees)})")
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
            st.markdown("**Livreur de la tournée** : " + (resume["livreur_nom"] or EN_ATTENTE))
            donner_a_un_livreur(resume, f"suivi_{resume['id']}", sauf=resume["livreur"])
            if resume["livreur"]:
                confirme = st.checkbox("Je confirme le retrait du livreur", key=f"confirme_retrait_{resume['id']}")
                if st.button("Annuler l'attribution (la tournée passe en attente)", disabled=not confirme,
                             key=f"retrait_{resume['id']}", width="stretch"):
                    if api("POST", f"/admin/tournees/{resume['id']}/desattribuer"):
                        annoncer("success", f"Attribution annulée : la tournée du "
                                            f"{date.fromisoformat(resume['date_tournee']):%d/%m/%Y} est en attente "
                                            f"et n'apparaît plus chez {resume['livreur_nom']}.")
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


def formulaire_tournee(jour: date) -> None:
    st.subheader("Attribuer une tournée")
    livreurs = livreurs_actifs()
    if not livreurs:
        st.info("Aucun livreur actif : créez d'abord un compte (onglet Livreurs).")
        return
    ids_import = st.session_state.get("ids_import", [])
    # Date et livreur d'abord : la liste indique qui a déjà une tournée ce jour-là
    col_date, col_livreur = st.columns([1, 2])
    date_tournee = col_date.date_input("Date de la tournée", value=max(jour, date.today()), min_value=date.today(),
                                       format="DD/MM/YYYY", key="form_date_tournee",
                                       help="Jour où le livreur effectuera la tournée.")
    occupes = occupation(date_tournee)
    livreur = col_livreur.selectbox("Livreur", sorted(livreurs, key=lambda c: c["identifiant"] in occupes),
                                    key="form_livreur", format_func=lambda c: libelle_livreur(c, occupes, date_tournee))
    existante = occupes.get(livreur["identifiant"])
    remplacer = False
    if existante:
        st.warning(f"{livreur['prenom']} {livreur['nom']} a déjà une tournée le {date_tournee:%d/%m/%Y} "
                   f"({existante['nb_clients']} clients, {STATUTS_TOURNEE.get(existante['statut'], existante['statut']).lower()}). "
                   "Choisissez un autre livreur, ou cochez la case ci-dessous pour la remplacer.")
        remplacer = st.checkbox(f"Remplacer sa tournée du {date_tournee:%d/%m/%Y} (ses {existante['nb_clients']} clients "
                                "ne seront plus planifiés ce jour-là)", key="form_remplacer")
    col_periode, col_info = st.columns([1, 2])
    periode = col_periode.selectbox("Période", list(PERIODES), key="form_periode")
    col_info.caption("Pour répartir une liste importée en plusieurs tournées réalisables, utilisez plutôt la "
                     "rubrique « Répartition » (tableau des tournées proposées, détail, choix du livreur).")
    texte = st.text_area("Identifiants des clients (un par ligne ; limite selon la zone et la période)",
                         value="\n".join(ids_import[:20]), height=130, key="form_ids",
                         help="Pré-rempli avec les clients du dernier import. L'aperçu ci-dessous se met à jour "
                              "quand vous quittez le champ.")
    ids = list(dict.fromkeys(x.strip() for x in texte.replace(",", "\n").splitlines() if x.strip()))
    apercu = None
    if ids:
        apercu = api("POST", "/admin/tournees/apercu", json={"clients": ids[:40], "periode": PERIODES[periode]})
    if apercu:
        apercu_formulaire(apercu, len(ids))
    trop = bool(apercu) and len(ids) > apercu["limite"]
    nom = f"{livreur['prenom']} {livreur['nom']}"
    if st.button(f"Attribuer la tournée du {date_tournee:%d/%m/%Y} à {nom}", type="primary", width="stretch",
                 disabled=not ids or trop or (bool(existante) and not remplacer)):
        t = api("POST", "/admin/tournees", attendu=(201,), timeout=30, json={
            "livreur_id": livreur["id"], "clients": ids, "date_tournee": date_tournee.isoformat(),
            "periode": PERIODES[periode], "remplacer": remplacer})
        if t:
            st.session_state.jour_a_afficher = date.fromisoformat(t["date_tournee"])
            if t.get("avertissements"):
                annoncer("warning", "Tournée créée, à vérifier : " + " ".join(t["avertissements"]))
            annoncer("success", f"Tournée du {date.fromisoformat(t['date_tournee']):%d/%m/%Y} attribuée à "
                                f"{t['livreur_nom']} le {date_heure(t['attribuee_le'])} : {t['nb_clients']} clients "
                                f"(limite {t['max_clients']}). Le livreur l'optimise depuis son écran.")


def apercu_formulaire(a: dict, nb_saisis: int) -> None:
    """Tableau de la tournée avant attribution : heure visée, créneau, client, code postal, ville."""
    with st.container(border=True):
        depart = (a["depart_au_plus_tard"] or "")[:5]
        charge = " · ".join(f"{h} h : {nb}" for h, nb in a["charge_par_heure"].items())
        st.markdown(f"**Aperçu de la tournée** — {len(a['clients'])} client(s) / limite {a['limite']} "
                    f"({a['zone'] or 'zone non définie'}, {a['debut_periode'][:5]}–{a['fin_periode'][:5]})"
                    + (f" · départ du dépôt avant **{depart}**" if depart else ""))
        if charge:
            st.caption(f"Clients prévus par heure — {charge}. Ordre horaire ; le livreur optimisera l'ordre "
                       "géographique.")
        refuses = set(a["refuses"])
        st.dataframe(pd.DataFrame([{
            "Passage visé": f"{c['heure_visee']} h – {c['heure_visee'] + 1} h" if c["heure_visee"] is not None
            else "créneau saturé",
            "Créneau": f"{(c['creneau_debut'] or '')[:5]}–{(c['creneau_fin'] or '')[:5]}",
            "Client": c["nom"], "Code postal": c["code_postal"], "Ville": c["ville"],
            "Camion": "hayon" if c["type_camion_requis"] == "hayon" else "normal", "Identifiant": c["id_client"]}
            for c in a["clients"]]), hide_index=True, width="stretch")
        if nb_saisis > a["limite"]:
            st.error(f"{nb_saisis} clients pour une limite de {a['limite']} : retirez-en {nb_saisis - a['limite']}.")
        if refuses:
            st.warning(f"{len(refuses)} client(s) au-delà de 2 par heure sur leur créneau : risque de retard.")
        if depart and depart < a["debut_periode"][:5]:
            st.warning(f"Départ du dépôt nécessaire avant {depart}, avant le début de la période.")
        if a["inconnus"]:
            st.error("Identifiants inconnus : " + ", ".join(a["inconnus"]))
        if a["non_localises"]:
            st.error("Clients sans position (géocodage à faire) : " + ", ".join(a["non_localises"]))


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


def tournees_proposees_restantes() -> list[dict]:
    """Tournées proposées dans « Répartition » et pas encore attribuées (gardées pour la session)."""
    prop = st.session_state.get("proposition")
    if not prop:
        return []
    fiches = {c["id_client"]: c for g in prop["groupes"] for c in g["clients"]}
    aff = st.session_state.get("affectation") or {c["id_client"]: g["numero"] for g in prop["groupes"]
                                                  for c in g["clients"]}
    attribuees = st.session_state.get("attribuees", {})
    restantes = []
    for g in prop["groupes"]:
        ids = [i for i, n in aff.items() if n == g["numero"]]
        if ids and g["numero"] not in attribuees:
            restantes.append({**g, "ids": ids, "date": prop["date_tournee"],
                              "creneaux": sorted(fiches[i]["creneau_debut"] or "07:00" for i in ids)})
    return restantes


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

    # ---- tournées à attribuer (en attente + proposées dans « Répartition »)
    bac: dict[str, dict] = {}
    for t in p["a_attribuer"]:
        bac[f"attente:{t['id']}"] = {
            "nature": "attente", "id": t["id"], "date": t["date_tournee"], "zone": t["zone"],
            "plage": plage_texte(t["heure_depart"], t["heure_fin"]), "nb": t["nb_clients"],
            "camion": t["type_camion_requis"] or "normal", "origine": "attribution annulée"}
    for g in tournees_proposees_restantes():
        if date.fromisoformat(g["date"]) >= aujourdhui:
            bac[f"prop:{g['numero']}"] = {
                "nature": "proposee", "numero": g["numero"], "date": g["date"], "zone": g["zone"],
                "plage": LIBELLES_PERIODE[g["periode"]], "nb": len(g["ids"]), "camion": g["type_camion"],
                "periode": g["periode"], "ids": g["ids"], "origine": f"proposée n° {g['numero']}",
                "depart": (g["depart_au_plus_tard"] or "")[:5]}

    def libelle_bac(cle: str) -> str:
        if cle == "":
            return "— aucune (consulter le planning) —"
        b = bac[cle]
        j = date.fromisoformat(b["date"])
        return (f"{JOURS_SEMAINE[j.weekday()]} {j:%d/%m} · {b['plage']} · {b['zone'] or 'zone ?'} · {b['nb']} clients · "
                f"camion {b['camion']} ({b['origine']})")

    with st.container(border=True):
        st.markdown(f"**Tournées à attribuer ({len(bac)})**")
        if not bac:
            st.caption("Aucune tournée à attribuer. Préparez des tournées dans « Répartition » (import puis "
                       "« Classer et proposer ») : elles apparaîtront ici.")
            choix = None
        else:
            cle = st.radio("Tournée à attribuer", [""] + list(bac), format_func=libelle_bac, key="planning_choix",
                           label_visibility="collapsed")
            choix = bac.get(cle)
            if choix:
                j = date.fromisoformat(choix["date"])
                st.info(f"Cliquez sur **« Attribuer »** dans la colonne du **{JOURS_SEMAINE[j.weekday()]} {j:%d/%m}**, "
                        f"sur la ligne du livreur voulu (camion {choix['camion']}, libre ce jour-là).")
                if not (debut <= j <= debut + timedelta(days=6)):
                    st.warning("Cette tournée n'est pas dans la semaine affichée : changez de semaine.")

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
                    st.rerun()
            elif case:
                col.button(f":violet[Trajets · {case['nb_etapes']}]", key=cle, width="stretch", disabled=True,
                           icon=":material/route:",
                           help="Journée de trajets saisie par le livreur (sans tournée).")
            elif choix and choix["date"] == j.isoformat() and j >= aujourdhui:
                if l["type_camion"] == choix["camion"]:
                    if col.button("Attribuer", key=cle, type="primary", width="stretch", icon=":material/add:",
                                  help=f"Donner cette tournée à {l['nom']}"):
                        attribuer_depuis_planning(choix, l, j)
                else:
                    col.button(":gray[camion ≠]", key=cle, width="stretch", disabled=True,
                               help=f"Cette tournée exige un camion {choix['camion']}.")
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


def attribuer_depuis_planning(choix: dict, livreur: dict, jour: date) -> None:
    if choix["nature"] == "attente":
        ok, res = api_brut("POST", f"/admin/tournees/{choix['id']}/attribuer", json={"livreur_id": livreur["id"]})
    else:
        ok, res = api_brut("POST", "/admin/tournees", json={
            "livreur_id": livreur["id"], "clients": choix["ids"], "date_tournee": choix["date"],
            "periode": choix["periode"], "remplacer": False})
    if not ok:
        st.error(f"Tournée non attribuée : {res}")
        return
    if choix["nature"] == "proposee":
        st.session_state.setdefault("attribuees", {})[choix["numero"]] = livreur["nom"]
        places = set(choix["ids"])
        st.session_state.liste_repartition = [i for i in st.session_state.get("liste_repartition", [])
                                              if i not in places]
    st.session_state.pop("planning_choix", None)
    avertissements = res.get("avertissements", []) if isinstance(res, dict) else []
    if avertissements:
        annoncer("warning", f"Tournée du {jour:%d/%m} attribuée à {livreur['nom']}, à vérifier : " + " ".join(avertissements))
    annoncer("success", f"Tournée du {jour:%d/%m/%Y} ({choix['plage']}, {choix['nb']} clients) attribuée à "
                        f"{livreur['nom']}. Elle apparaît dans son application.")


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


def onglet_import() -> None:
    st.write("Fichier au format de l'échantillon **tournees_esis.csv** (séparateur « , » ou « ; ») : "
             "`id_client`, `nom_client`, `adresse`, `creneau_horaire_prefere` obligatoires ; "
             "`telephone`, `digicode`, `notes_livraison` facultatifs.")
    fichier = st.file_uploader("Fichier CSV", type=["csv"])
    max_geo = st.slider("Nouvelles adresses à géocoder tout de suite", 0, 60, 15,
                        help="Nominatim : 1 adresse par seconde. Le reste peut être géocodé ensuite par lots.")
    if fichier is not None and st.button("Importer", type="primary"):
        if fichier.size > 2_000_000:
            st.error("Fichier trop volumineux (2 Mo maximum).")
            return
        contenu = lire_fichier(fichier)
        if contenu is None:
            return
        with st.spinner("Import, nettoyage et géocodage des nouvelles adresses..."):
            bilan = api("POST", "/admin/import-csv", timeout=180, json={
                "nom_fichier": fichier.name[:100], "contenu": contenu, "max_geocodages": max_geo})
        if bilan:
            st.session_state.bilan_import = bilan
            st.session_state.ids_import = bilan["ids_valides"]
            st.session_state.liste_repartition = list(bilan["ids_valides"])     # prêts pour la répartition
            st.session_state.pop("proposition", None)
    bilan = st.session_state.get("bilan_import")
    if bilan:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Lignes lues", bilan["lignes"])
        c2.metric("Clients créés", bilan["crees"])
        c3.metric("Mis à jour", bilan["mis_a_jour"])
        c4.metric("Rejetées", len(bilan["rejets"]))
        geo = bilan["geocodage"]
        st.caption(f"Géocodage : {geo['traites']} adresse(s) traitée(s) "
                   f"({', '.join(f'{k} {v}' for k, v in geo['precisions'].items()) or 'aucune'}) ; "
                   f"{geo['restants']} en attente. {bilan['avec_avertissement']} complément(s) d'adresse "
                   "déplacé(s) dans les notes chiffrées.")
        if geo["service_indisponible"]:
            st.warning("Service de géocodage indisponible : réessayez plus tard.")
        if bilan["rejets"]:
            st.dataframe(pd.DataFrame(bilan["rejets"]).rename(columns={
                "ligne": "Ligne", "id_client": "Identifiant", "motif": "Motif du rejet"}),
                hide_index=True, width="stretch")
        if bilan["ids_valides"]:
            st.info(f"{len(bilan['ids_valides'])} client(s) prêts : ouvrez la rubrique « Répartition » pour les "
                    "classer par secteur et plage horaire et les attribuer en tournées.")
    if st.button("Géocoder les adresses en attente (lot de 20)"):
        with st.spinner("Géocodage (1 adresse par seconde)..."):
            res = api("POST", "/admin/clients/geocoder", timeout=180, json={"maximum": 20})
        if res:
            st.success(f"{res['traites']} adresse(s) traitée(s), {res['restants']} encore en attente.")


# --------------------------------------------------------------------------- répartition en tournées
LIBELLES_PERIODE = {"matin": "Matin", "apres-midi": "Après-midi", "soiree": "Soirée", "journee": "Journée complète"}
RETIRER = "Retirer"


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


HEURES = list(range(7, 22))            # colonnes 7 h … 21 h (la dernière couvre 21 h – 22 h)
BLEU = (31, 78, 121)                   # couleur de la marque Road Optim


def _en_heures(valeur: str | None, defaut: float) -> float:
    if not valeur:
        return defaut
    h, m = valeur.split(":")[:2]
    return int(h) + int(m) / 60


def grille_cp_heures(clients: list[dict]) -> pd.DataFrame:
    """Une ligne par code postal, une colonne par heure : nombre de clients dont le créneau couvre
    cette heure (un client 8 h – 12 h compte dans 8 h, 9 h, 10 h et 11 h). Sans créneau : 7 h – 22 h."""
    lignes: dict[str, dict] = {}
    for c in clients:
        debut, fin = _en_heures(c["creneau_debut"], 7), _en_heures(c["creneau_fin"], 22)
        ligne = lignes.setdefault(f"{c['code_postal']} {c['ville'].title()}", {"Clients": 0, **{h: 0 for h in HEURES}})
        ligne["Clients"] += 1
        for h in HEURES:
            if debut < h + 1 and fin > h:
                ligne[h] += 1
    grille = pd.DataFrame.from_dict(lignes, orient="index").sort_index()
    grille.loc["Total"] = grille.sum()
    grille.columns = ["Clients"] + [f"{h} h" for h in HEURES]
    grille.index.name = "Code postal"
    return grille


def style_grille(grille: pd.DataFrame):
    """Cases colorées selon le nombre de clients (plus foncé = plus chargé) ; zéros masqués."""
    heures = [f"{h} h" for h in HEURES]
    maximum = max(1, int(grille.drop(index="Total")[heures].to_numpy().max()) if len(grille) > 1 else 1)

    def couleur(v):
        if not v:
            return "color: transparent"
        a = 0.15 + 0.85 * min(v, maximum) / maximum
        texte = "#FFFFFF" if a > 0.55 else "#16213A"
        return f"background-color: rgba({BLEU[0]}, {BLEU[1]}, {BLEU[2]}, {a:.2f}); color: {texte}"

    corps = pd.IndexSlice[grille.index.drop("Total"), heures]
    return (grille.style.map(couleur, subset=corps)
            .map(lambda v: "font-weight: 600", subset=pd.IndexSlice[:, ["Clients"]])
            .map(lambda v: "background-color: #EEF1EA; font-weight: 600", subset=pd.IndexSlice[["Total"], :])
            .format(lambda v: "" if not v else str(int(v))))


def apercu_commandes(liste: list[str]) -> None:
    """Vue d'ensemble de la liste à livrer : où (code postal) et quand (heures des créneaux)."""
    cle = hash(tuple(liste))
    cache = st.session_state.get("apercu")
    if not cache or cache[0] != cle:
        donnees = api("POST", "/admin/repartition/apercu", json={"clients": liste[:500]})
        if donnees is None:
            return
        st.session_state.apercu = cache = (cle, donnees)
    clients = cache[1]
    if not clients:
        return
    plages = {"Matin": 0, "Après-midi": 0, "Soirée": 0}
    for c in clients:
        milieu = (_en_heures(c["creneau_debut"], 7) + _en_heures(c["creneau_fin"], 22)) / 2
        plages["Matin" if milieu < 13 else "Après-midi" if milieu < 18 else "Soirée"] += 1
    nb_cp = len({c["code_postal"] for c in clients})
    palettes = sum(c["type_camion_requis"] == "hayon" for c in clients)
    non_localises = sum(not c["localise"] for c in clients)
    st.markdown(f"**Aperçu des commandes** — {len(clients)} clients, {nb_cp} code(s) postal(aux) · "
                + " · ".join(f"{k} {v}" for k, v in plages.items())
                + (f" · {palettes} palette(s)" if palettes else "")
                + (f" · {non_localises} non localisé(s)" if non_localises else ""))
    grille = grille_cp_heures(clients)
    st.dataframe(style_grille(grille), width="stretch", height=min(38 * (len(grille) + 1) + 3, 560))
    st.caption("Chaque case indique combien de clients de ce code postal peuvent être livrés pendant cette heure "
               "(selon leur créneau). Plus la case est foncée, plus le secteur est chargé à ce moment-là.")
    choix = st.multiselect("Détail par code postal", sorted({c["code_postal"] for c in clients}),
                           placeholder="Choisir un ou plusieurs codes postaux")
    if choix:
        detail = sorted((c for c in clients if c["code_postal"] in choix),
                        key=lambda c: (c["code_postal"], c["creneau_debut"] or "", c["creneau_fin"] or ""))
        st.dataframe(pd.DataFrame([{
            "Code postal": c["code_postal"], "Ville": c["ville"].title(), "Client": c["nom"],
            "Créneau": f"{(c['creneau_debut'] or '07:00')[:5]} – {(c['creneau_fin'] or '22:00')[:5]}",
            "Camion": "hayon (palette)" if c["type_camion_requis"] == "hayon" else "normal",
            "Identifiant": c["id_client"]} for c in detail]), hide_index=True, width="stretch")


def changer_liste(ids: list[str]) -> None:
    st.session_state.liste_repartition = list(dict.fromkeys(ids))
    for cle in ("proposition", "affectation", "attribuees"):
        st.session_state.pop(cle, None)


def onglet_repartition() -> None:
    st.caption("Importez une liste de clients (rubrique Import CSV), complétez-la si besoin, puis laissez "
               "l'application les classer par secteur (zone et code postal), plage horaire et type de camion. "
               "Elle propose une tournée par groupe, dans la limite de chaque zone, avec un livreur disponible : "
               "vous ajustez, puis vous validez.")
    if bilan := st.session_state.pop("bilan_repartition", None):
        for ok, texte in bilan:
            (st.warning if ok == "alerte" else st.success if ok else st.error)(texte)
    st.session_state.setdefault("liste_repartition", list(st.session_state.get("ids_import", [])))
    liste = st.session_state.liste_repartition

    # ---- 1. la liste
    st.subheader(f"1. Clients à répartir ({len(liste)})")
    c1, c2 = st.columns(2)
    ids_import = st.session_state.get("ids_import", [])
    if c1.button(f"Reprendre le dernier import ({len(ids_import)})", disabled=not ids_import, width="stretch"):
        changer_liste(ids_import)
        st.rerun()
    if c2.button("Vider la liste", disabled=not liste, width="stretch"):
        changer_liste([])
        st.rerun()
    if liste:
        st.caption("Identifiants : " + ", ".join(liste[:12]) + (f" … (+{len(liste) - 12})" if len(liste) > 12 else ""))
    with st.expander("Ajouter des clients déjà enregistrés"):
        col_a, col_b = st.columns(2)
        with col_a, st.form("rep_ajout_ids", clear_on_submit=True):
            texte = st.text_area("Identifiants (un par ligne)", height=110)
            if st.form_submit_button("Ajouter ces identifiants"):
                nouveaux = [x.strip() for x in texte.replace(",", "\n").splitlines() if x.strip()]
                changer_liste(liste + nouveaux)
                st.rerun()
        with col_b:
            with st.form("rep_recherche"):
                recherche = st.text_input("Code postal, ville ou nom", placeholder="69100")
                chercher = st.form_submit_button("Rechercher")
            if chercher and recherche.strip():
                st.session_state.rep_resultats = api("GET", "/admin/clients",
                                                     params={"recherche": recherche.strip(), "limite": 100}) or []
            resultats = [c for c in st.session_state.get("rep_resultats", []) if c["id_client"] not in liste]
            if resultats:
                choix = st.multiselect(f"{len(resultats)} client(s) trouvé(s), hors liste", resultats, default=resultats,
                                       format_func=lambda c: f"{c['code_postal']} {c['ville']} — {c['nom']} ({c['id_client']})")
                if st.button("Ajouter la sélection", disabled=not choix):
                    changer_liste(liste + [c["id_client"] for c in choix])
                    st.session_state.pop("rep_resultats", None)
                    st.rerun()

    if liste:
        apercu_commandes(liste)

    # ---- 2. classer et proposer
    st.subheader("2. Classer et proposer des tournées")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    jour = c1.date_input("Date des tournées", value=date.today(), min_value=date.today(), format="DD/MM/YYYY",
                         key="rep_date")
    continue_ = c2.toggle("Journée complète (une seule plage)", key="rep_continue",
                          help="Par défaut, les clients sont séparés en matin, après-midi et soirée "
                               "selon leur créneau, pour éviter de longues attentes. Une tournée peut "
                               "atteindre la limite de sa zone (20 au plus) seulement sur la journée complète.")
    par_heure = c3.number_input("Clients maximum par heure", min_value=1, max_value=6, value=2, key="rep_par_heure",
                                help="Une tournée ne prévoit jamais plus de clients dans une même heure "
                                     "(8 h – 9 h, 9 h – 10 h…) : elle reste réalisable par le livreur.")
    if c4.button("Classer et proposer", type="primary", disabled=not liste, width="stretch"):
        prop = api("POST", "/admin/repartition", timeout=30, json={
            "date_tournee": jour.isoformat(), "clients": liste[:500], "journee_continue": continue_,
            "clients_par_heure": int(par_heure)})
        if prop is not None:
            st.session_state.proposition = prop
            for cle in [k for k in st.session_state if str(k).startswith(("rep_g", "rep_l"))]:
                del st.session_state[cle]
            st.session_state.pop("affectation", None)
            st.session_state.pop("attribuees", None)
            st.rerun()
    prop = st.session_state.get("proposition")
    if prop:
        proposition(prop)


def proposition(prop: dict) -> None:
    jour = date.fromisoformat(prop["date_tournee"])
    groupes = prop["groupes"]
    if prop["exclus"]:
        with st.expander(f"{len(prop['exclus'])} client(s) écarté(s)", expanded=True):
            st.dataframe(pd.DataFrame(prop["exclus"]).rename(columns={"id_client": "Client", "motif": "Motif"}),
                         hide_index=True, width="stretch")
    if not groupes:
        st.info("Aucun client à répartir.")
        return

    # Classement : code postal × plage horaire
    lignes = [{"Code postal": f"{c['code_postal']} {c['ville']}", "Plage": LIBELLES_PERIODE[g["periode"]]}
              for g in groupes for c in g["clients"]]
    tableau = pd.crosstab(pd.DataFrame(lignes)["Code postal"], pd.DataFrame(lignes)["Plage"], margins=True,
                          margins_name="Total")
    ordre = [p for p in LIBELLES_PERIODE.values() if p in tableau.columns] + ["Total"]
    with st.expander(f"Classement par code postal et plage horaire ({len(lignes)} clients)"):
        st.dataframe(tableau[ordre], width="stretch")

    st.subheader(f"3. Tournées proposées pour le {jour:%d/%m/%Y} ({len(groupes)})")
    st.caption("Ouvrez une tournée pour la visualiser (heure visée, créneau, client, code postal…), choisissez "
               "le livreur puis « Attribuer ».")
    par_numero = {g["numero"]: g for g in groupes}
    fiches = {c["id_client"]: c for g in groupes for c in g["clients"]}
    aff = st.session_state.setdefault("affectation", {c["id_client"]: g["numero"] for g in groupes for c in g["clients"]})
    attribuees = st.session_state.setdefault("attribuees", {})

    def membres(n: int) -> list[dict]:
        return sorted((fiches[i] for i, m in aff.items() if m == n),
                      key=lambda c: (c["heure_visee"] if c["heure_visee"] is not None else 99, c["creneau_debut"] or ""))

    visibles = [g for g in groupes if membres(g["numero"]) or g["numero"] in attribuees]
    numeros = [str(g["numero"]) for g in visibles if g["numero"] not in attribuees]
    occupes, livreurs = occupation(jour), livreurs_actifs()          # une seule lecture pour toute la liste
    restantes = len(numeros)
    st.caption(f"{restantes} tournée(s) à attribuer, {len(visibles) - restantes} attribuée(s).")
    for g in visibles:
        n, liste_n = g["numero"], membres(g["numero"])
        depart = (g["depart_au_plus_tard"] or "")[:5]
        etat = f"[Attribuée à {attribuees[n]}] " if n in attribuees else ""
        titre = (f"{etat}Tournée {n} — {g['zone'] or 'zone non définie'} · {LIBELLES_PERIODE[g['periode']]} · camion "
                 f"{g['type_camion']} · {len(liste_n)} / {g['limite']} clients · CP "
                 f"{', '.join(sorted({c['code_postal'] for c in liste_n}))}"
                 + (f" · départ du dépôt avant {depart}" if depart else ""))
        with st.expander(titre, expanded=False):
            detail_proposee(par_numero[n], jour, liste_n, numeros, aff, attribuees, occupes, livreurs)


def detail_proposee(g: dict, jour: date, liste_n: list[dict], numeros: list[str], aff: dict, attribuees: dict,
                    occupes: dict, livreurs: list[dict]) -> None:
    n = g["numero"]
    depart = (g["depart_au_plus_tard"] or "")[:5]
    charge: dict[int, int] = {}
    for c in liste_n:
        if c["heure_visee"] is not None:
            charge[c["heure_visee"]] = charge.get(c["heure_visee"], 0) + 1
    st.caption("Clients prévus par heure — " + " · ".join(f"{h} h : {nb}" for h, nb in sorted(charge.items()))
               + (f" · départ du dépôt avant {depart}" if depart else "")
               + ". L'ordre géographique sera optimisé par le livreur.")
    df = pd.DataFrame([{
        "Tournée": str(n),
        "Passage visé": f"{c['heure_visee']} h – {c['heure_visee'] + 1} h" if c["heure_visee"] is not None else "",
        "Créneau": f"{(c['creneau_debut'] or '')[:5]}–{(c['creneau_fin'] or '')[:5]}",
        "Client": c["nom"], "Code postal": c["code_postal"], "Ville": c["ville"], "id": c["id_client"]}
        for c in liste_n])
    if n in attribuees:
        st.success(f"Tournée attribuée à {attribuees[n]}. Retrouvez-la dans « Tournées du jour ».")
        st.dataframe(df.drop(columns=["Tournée"]), hide_index=True, width="stretch")
        return
    cle = f"rep_g{n}_{abs(hash(tuple(c['id_client'] for c in liste_n)))}"
    edite = st.data_editor(
        df, key=cle, hide_index=True, width="stretch",
        disabled=["Passage visé", "Client", "Code postal", "Ville", "Créneau", "id"],
        column_config={"Tournée": st.column_config.SelectboxColumn(
            "Tournée", options=numeros + [RETIRER], required=True,
            help="Déplacer le client vers une autre tournée proposée, ou le retirer."),
            "id": st.column_config.TextColumn("Identifiant")})
    deplaces = {l["id"]: l["Tournée"] for _, l in edite.iterrows() if l["Tournée"] != str(n)}
    if deplaces:
        for i, cible in deplaces.items():
            aff[i] = RETIRER if cible == RETIRER else int(cible)
        st.rerun()
    if len(liste_n) > g["limite"]:
        st.warning(f"{len(liste_n)} clients pour une limite de {g['limite']} : déplacez-en vers une autre tournée.")
    if any(nb > 2 for nb in charge.values()):
        st.warning("Plus de 2 clients prévus sur une même heure : risque de retard.")

    candidats = sorted((c for c in livreurs if c["type_camion"] == g["type_camion"]),
                       key=lambda c: c["identifiant"] in occupes)
    if not candidats:
        st.warning(f"Aucun livreur avec un camion {g['type_camion']}.")
        return
    defaut = next((k for k, c in enumerate(candidats)
                   if c["id"] == g["livreur_propose_id"] and c["identifiant"] not in occupes), 0)
    col1, col2 = st.columns([3, 1])
    livreur = col1.selectbox("Livreur", candidats, index=defaut, key=f"rep_l{n}",
                             format_func=lambda c: libelle_livreur(c, occupes, jour))
    pris = livreur["identifiant"] in occupes
    if pris:
        st.warning(f"{livreur['prenom']} {livreur['nom']} a déjà une tournée le {jour:%d/%m/%Y} : choisissez un "
                   "autre livreur.")
    col2.markdown("&nbsp;")
    if col2.button("Attribuer", key=f"rep_attribuer_{n}", type="primary", width="stretch",
                   disabled=pris or not liste_n or len(liste_n) > g["limite"]):
        ok, res = api_brut("POST", "/admin/tournees", json={
            "livreur_id": livreur["id"], "clients": [c["id_client"] for c in liste_n],
            "date_tournee": jour.isoformat(), "periode": g["periode"], "remplacer": False})
        nom = f"{livreur['prenom']} {livreur['nom']}"
        if ok:
            attribuees[n] = nom
            places = {c["id_client"] for c in liste_n}
            st.session_state.liste_repartition = [i for i in st.session_state.liste_repartition if i not in places]
            st.session_state.bilan_repartition = (
                [(True, f"Tournée {n} attribuée à {nom} : {len(liste_n)} clients, le {jour:%d/%m/%Y}.")]
                + [("alerte", f"Tournée {n} : {m}") for m in res.get("avertissements", [])])
            st.session_state.jour_a_afficher = jour
            st.rerun()
        st.error(f"Tournée {n} non attribuée : {res}")


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
        "Tableau de bord": onglet_tableau_de_bord,
        "Planning": onglet_planning,
        "Tournées du jour": onglet_tournees,
        "Import CSV": onglet_import,
        "Répartition": onglet_repartition,
        "Livreurs": onglet_livreurs,
        "Clients": onglet_clients,
        "Sécurité": onglet_securite,
    }[onglet]()
