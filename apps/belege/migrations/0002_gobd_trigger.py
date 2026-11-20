"""GoBD-Schutz auf Datenbankebene (PostgreSQL).

Selbst direkte SQL-Updates (an der Anwendung vorbei) können festgeschriebene oder stornierte Belege
und Positionen nicht freigegebener Belege nicht mehr verändern oder löschen.
Erlaubt bleibt nur der Übergang FESTGESCHRIEBEN → STORNIERT mit unverändertem Inhalt.
"""
from django.db import migrations

SQL_AN = r"""
CREATE OR REPLACE FUNCTION belege_beleg_gobd_schutz() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'ENTWURF' THEN
      RAISE EXCEPTION 'GoBD: Beleg % (Status %) darf nicht gelöscht werden', OLD.nummer, OLD.status;
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'STORNIERT' THEN
    RAISE EXCEPTION 'GoBD: Stornierter Beleg % ist unveränderbar', OLD.nummer;
  END IF;
  IF OLD.status = 'FESTGESCHRIEBEN' THEN
    IF NEW.status <> 'STORNIERT'
       OR NEW.nummer IS DISTINCT FROM OLD.nummer OR NEW.belegart IS DISTINCT FROM OLD.belegart
       OR NEW.partner_id IS DISTINCT FROM OLD.partner_id OR NEW.partner_daten IS DISTINCT FROM OLD.partner_daten
       OR NEW.belegdatum IS DISTINCT FROM OLD.belegdatum OR NEW.leistungsdatum IS DISTINCT FROM OLD.leistungsdatum
       OR NEW.betreff IS DISTINCT FROM OLD.betreff OR NEW.kopftext IS DISTINCT FROM OLD.kopftext
       OR NEW.fusstext IS DISTINCT FROM OLD.fusstext OR NEW.waehrung IS DISTINCT FROM OLD.waehrung
       OR NEW.summe_netto IS DISTINCT FROM OLD.summe_netto OR NEW.summe_steuer IS DISTINCT FROM OLD.summe_steuer
       OR NEW.summe_brutto IS DISTINCT FROM OLD.summe_brutto
       OR NEW.festgeschrieben_am IS DISTINCT FROM OLD.festgeschrieben_am
       OR NEW.ketten_index IS DISTINCT FROM OLD.ketten_index OR NEW.pruefsumme IS DISTINCT FROM OLD.pruefsumme
       OR NEW.vorgaenger_pruefsumme IS DISTINCT FROM OLD.vorgaenger_pruefsumme
       OR NEW.pdf IS DISTINCT FROM OLD.pdf OR NEW.pdf_pruefsumme IS DISTINCT FROM OLD.pdf_pruefsumme
    THEN
      RAISE EXCEPTION 'GoBD: Festgeschriebener Beleg % ist unveränderbar (nur Storno erlaubt)', OLD.nummer;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS belege_beleg_gobd ON belege_beleg;
CREATE TRIGGER belege_beleg_gobd BEFORE UPDATE OR DELETE ON belege_beleg
  FOR EACH ROW EXECUTE FUNCTION belege_beleg_gobd_schutz();

CREATE OR REPLACE FUNCTION belege_position_gobd_schutz() RETURNS trigger AS $$
DECLARE st varchar;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT status INTO st FROM belege_beleg WHERE id = OLD.beleg_id;
  ELSE
    SELECT status INTO st FROM belege_beleg WHERE id = NEW.beleg_id;
  END IF;
  IF st IS NOT NULL AND st <> 'ENTWURF' THEN
    RAISE EXCEPTION 'GoBD: Positionen eines Belegs im Status % sind unveränderbar', st;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS belege_position_gobd ON belege_belegposition;
CREATE TRIGGER belege_position_gobd BEFORE INSERT OR UPDATE OR DELETE ON belege_belegposition
  FOR EACH ROW EXECUTE FUNCTION belege_position_gobd_schutz();
"""

SQL_AUS = """
DROP TRIGGER IF EXISTS belege_beleg_gobd ON belege_beleg;
DROP TRIGGER IF EXISTS belege_position_gobd ON belege_belegposition;
DROP FUNCTION IF EXISTS belege_beleg_gobd_schutz();
DROP FUNCTION IF EXISTS belege_position_gobd_schutz();
"""


def _ausfuehren(sql):
    def run(apps, schema_editor):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(sql, params=None)

    return run


class Migration(migrations.Migration):
    dependencies = [("belege", "0001_initial")]
    operations = [migrations.RunPython(_ausfuehren(SQL_AN), _ausfuehren(SQL_AUS))]
