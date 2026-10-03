"""Espace administrateur (module 4) : tableau de bord, tournées du jour, import CSV, livreurs,
clients (correction, RGPD), sécurité.

Appelé par app.py, qui fournit les outils communs (appel à l'API, session, carte de tournée).
Toutes les règles (droits, refus, journal) sont appliquées par l'API : cet écran ne fait qu'afficher.
"""
import json
from datetime import date, datetime, timezone

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

ONGLETS = ["Tableau de bord", "Tournées du jour", "Import CSV", "Livreurs", "Clients", "Sécurité"]
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
              "TRAJET_ETAPE_AJOUTEE", "TRAJET_ETAPE_SUPPRIMEE", "TRAJETS_OPTIMISES"]

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
def onglet_tournees() -> None:
    jour = st.date_input("Jour", value=date.today(), format="DD/MM/YYYY", key="tournees_jour")
    tournees = api("GET", "/admin/tournees", params={"date": jour.isoformat()})
    if tournees is None:
        return
    if tournees:
        st.dataframe(pd.DataFrame([{
            "Livreur": t["livreur_nom"], "Camion": f"{t['vehicule']} ({t['type_camion']})",
            "Zone": t["zone"], "Période": f"{(t['heure_depart'] or '')[:5]}–{(t['heure_fin'] or '')[:5]}",
            "Statut": STATUTS_TOURNEE.get(t["statut"], t["statut"]),
            "Clients": f"{t['nb_clients']} / {t['max_clients']}", "Pointés": t["nb_terminees"],
            "À reporter": t["nb_a_reporter"], "Optimisée": "oui" if t["optimisee"] else "non",
            "km": nombre_fr(t["distance_totale_km"]) if t["distance_totale_km"] is not None else "—"}
            for t in tournees]), hide_index=True, width="stretch")
        choix = st.selectbox("Suivre une tournée", tournees, index=None, placeholder="Choisir un livreur",
                             format_func=lambda t: f"{t['livreur_nom']} — {t['nb_clients']} clients")
        if choix:
            suivi_tournee(choix)
    else:
        st.info("Aucune tournée ce jour-là.")
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
    if resume["statut"] != "terminee":
        with st.form(f"ajout_existant_{resume['id']}", clear_on_submit=True):
            st.write("Ajouter un client déjà enregistré à cette tournée")
            ident = st.text_input("Identifiant du client (10 chiffres)", max_chars=10)
            if st.form_submit_button("Ajouter"):
                if api("POST", f"/admin/tournees/{resume['id']}/clients", attendu=(201,),
                       json={"id_client": ident.strip()}):
                    annoncer("success", "Client ajouté : le livreur doit relancer l'optimisation.")


def formulaire_tournee(jour: date) -> None:
    st.subheader("Attribuer une tournée")
    comptes = api("GET", "/admin/utilisateurs") or []
    livreurs = [c for c in comptes if c["role"] == "livreur" and c["actif"]]
    if not livreurs:
        st.info("Aucun livreur actif : créez d'abord un compte (onglet Livreurs).")
        return
    ids_import = st.session_state.get("ids_import", [])
    with st.form("nouvelle_tournee"):
        livreur = st.selectbox("Livreur", livreurs, format_func=lambda c: (
            f"{c['prenom']} {c['nom']} — " + (f"{c['vehicule']} ({c['type_camion']})" if c["vehicule"]
                                              else "sans camion")))
        periode = st.selectbox("Période", list(PERIODES))
        texte = st.text_area("Identifiants des clients (un par ligne, 20 au maximum)",
                             value="\n".join(ids_import[:20]), height=150,
                             help="Pré-rempli avec les clients du dernier import. Un client de l'onglet "
                                  "Clients peut aussi être ajouté par son identifiant.")
        remplacer = st.checkbox("Remplacer la tournée existante de ce livreur (si elle n'a pas commencé)")
        valider = st.form_submit_button("Attribuer la tournée", type="primary", width="stretch")
    if valider:
        ids = [x.strip() for x in texte.replace(",", "\n").splitlines() if x.strip()]
        if not ids:
            st.error("Indiquez au moins un client.")
            return
        t = api("POST", "/admin/tournees", attendu=(201,), timeout=30, json={
            "livreur_id": livreur["id"], "clients": ids, "date_tournee": jour.isoformat(),
            "periode": PERIODES[periode], "remplacer": remplacer})
        if t:
            annoncer("success", f"Tournée attribuée à {t['livreur_nom']} : {t['nb_clients']} clients "
                                f"(limite {t['max_clients']}). Le livreur l'optimise depuis son écran.")


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
            st.info(f"{len(bilan['ids_valides'])} client(s) prêts : ils sont pré-remplis dans "
                    "« Tournées du jour » pour l'attribution.")
    if st.button("Géocoder les adresses en attente (lot de 20)"):
        with st.spinner("Géocodage (1 adresse par seconde)..."):
            res = api("POST", "/admin/clients/geocoder", timeout=180, json={"maximum": 20})
        if res:
            st.success(f"{res['traites']} adresse(s) traitée(s), {res['restants']} encore en attente.")


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
        "Tournées du jour": onglet_tournees,
        "Import CSV": onglet_import,
        "Livreurs": onglet_livreurs,
        "Clients": onglet_clients,
        "Sécurité": onglet_securite,
    }[onglet]()
