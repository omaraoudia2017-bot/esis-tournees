"""Règles d'intégrité appliquées directement par PostgreSQL (triggers).

Même si un bug applicatif ou une requête SQL manuelle tente de contourner l'API,
la base refuse :
  1. de dépasser la limite d'effectif d'une tournée (10 à 20 clients) ;
  2. de modifier le journal de sécurité, ou d'en supprimer une entrée de moins d'un an.

Scripts idempotents (CREATE OR REPLACE / DROP IF EXISTS) : on peut les rejouer sans risque.
"""

LIMITE_CLIENTS_TOURNEE = ["""
CREATE OR REPLACE FUNCTION verifier_limite_clients_tournee() RETURNS trigger AS $$
DECLARE
    v_max smallint;
    v_nb  integer;
BEGIN
    -- Verrou sur la tournée : deux ajouts simultanés ne peuvent pas dépasser la limite
    SELECT max_clients INTO v_max FROM tournees WHERE id = NEW.tournee_id FOR UPDATE;

    SELECT count(*) INTO v_nb FROM livraisons
     WHERE tournee_id = NEW.tournee_id AND id IS DISTINCT FROM NEW.id;

    IF v_nb >= v_max THEN
        RAISE EXCEPTION USING
            ERRCODE = 'check_violation',
            MESSAGE = 'Limite de ' || v_max || ' clients atteinte pour la tournee ' || NEW.tournee_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
""", """
DROP TRIGGER IF EXISTS trg_limite_clients_tournee ON livraisons
""", """
CREATE TRIGGER trg_limite_clients_tournee
    BEFORE INSERT OR UPDATE OF tournee_id ON livraisons
    FOR EACH ROW EXECUTE FUNCTION verifier_limite_clients_tournee()
"""]

JOURNAL_AJOUT_SEUL = ["""
CREATE OR REPLACE FUNCTION proteger_journal_securite() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'insufficient_privilege',
            MESSAGE = 'Le journal de securite ne peut pas etre modifie';
    END IF;
    -- Suppression autorisée uniquement pour la purge RGPD (entrées de plus d'un an)
    IF OLD.horodatage > now() - interval '1 year' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'insufficient_privilege',
            MESSAGE = 'Suppression interdite : entree de moins d''un an';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
""", """
DROP TRIGGER IF EXISTS trg_journal_ajout_seul ON journal_securite
""", """
CREATE TRIGGER trg_journal_ajout_seul
    BEFORE UPDATE OR DELETE ON journal_securite
    FOR EACH ROW EXECUTE FUNCTION proteger_journal_securite()
"""]

# Évolutions de tables existantes (create_all ne modifie jamais une table déjà créée).
# Idempotent : IF NOT EXISTS. Permet de faire évoluer le schéma sans vider la base.
MIGRATIONS = [
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS totp_dernier_pas BIGINT",   # Jour 3
    "ALTER TABLE tournees ADD COLUMN IF NOT EXISTS heure_depart TIME",             # Jour 7
    "ALTER TABLE tournees ADD COLUMN IF NOT EXISTS heure_fin TIME",                # Jour 7
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS sessions_invalidees_le TIMESTAMPTZ",  # Jour 12
    "ALTER TYPE precision_geocodage ADD VALUE IF NOT EXISTS 'manuelle'",           # Jour 12
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS conditions_version VARCHAR(10)",          # CGU
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS conditions_acceptees_le TIMESTAMPTZ",     # CGU
    # Livreur interne / prestataire et adresse du domicile (chiffrée)
    "DO $$ BEGIN CREATE TYPE type_livreur AS ENUM ('interne', 'prestataire'); "
    "EXCEPTION WHEN duplicate_object THEN NULL; END $$",
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS type_livreur type_livreur",
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS domicile_adresse TEXT",
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS domicile_position TEXT",
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS domicile_maj_le TIMESTAMPTZ",
    # Comptes existants : prestataire par défaut (départ et retour au dépôt, comme avant)
    "UPDATE utilisateurs SET type_livreur = 'prestataire' WHERE role = 'livreur' AND type_livreur IS NULL",
    # Activation du compte : code à usage unique, mot de passe choisi par l'utilisateur
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS mdp_a_definir BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS activation_expire_le TIMESTAMPTZ",
    # Tournée « en attente » : attribution annulée, sans livreur jusqu'à la nouvelle attribution
    "ALTER TABLE tournees ALTER COLUMN livreur_id DROP NOT NULL",
]

# Une instruction SQL par élément (exécutées une à une)
TOUS_LES_TRIGGERS = LIMITE_CLIENTS_TOURNEE + JOURNAL_AJOUT_SEUL
