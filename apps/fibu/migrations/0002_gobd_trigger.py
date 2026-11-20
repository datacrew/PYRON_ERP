"""GoBD-Schutz des Buchungsjournals auf Datenbankebene (PostgreSQL).

* Festgeschriebene Buchungen und ihre Zeilen sind unveränderbar und unlöschbar (auch per direktem SQL).
  Korrektur ausschließlich über eine neue Stornobuchung.
* In gesperrten Perioden können keine Buchungen angelegt, geändert oder gelöscht werden.
"""
from django.db import migrations

SQL_AN = r"""
CREATE OR REPLACE FUNCTION fibu_buchung_gobd_schutz() RETURNS trigger AS $$
DECLARE gesperrt boolean;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.status = 'FESTGESCHRIEBEN' THEN
    RAISE EXCEPTION 'GoBD: Festgeschriebene Buchung % ist unveränderbar (Korrektur nur per Storno)', OLD.nummer;
  END IF;
  IF TG_OP = 'DELETE' THEN
    SELECT p.gesperrt INTO gesperrt FROM fibu_periode p WHERE p.id = OLD.periode_id;
    IF gesperrt THEN
      RAISE EXCEPTION 'Periodensperre: Buchung % liegt in einer gesperrten Periode', OLD.nummer;
    END IF;
    RETURN OLD;
  END IF;
  SELECT p.gesperrt INTO gesperrt FROM fibu_periode p WHERE p.id = NEW.periode_id;
  IF gesperrt AND (TG_OP = 'INSERT' OR NEW.status = OLD.status) THEN
    RAISE EXCEPTION 'Periodensperre: Buchung % liegt in einer gesperrten Periode', NEW.nummer;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fibu_buchung_gobd ON fibu_buchung;
CREATE TRIGGER fibu_buchung_gobd BEFORE INSERT OR UPDATE OR DELETE ON fibu_buchung
  FOR EACH ROW EXECUTE FUNCTION fibu_buchung_gobd_schutz();

CREATE OR REPLACE FUNCTION fibu_zeile_gobd_schutz() RETURNS trigger AS $$
DECLARE st varchar; gesperrt boolean; bid bigint;
BEGIN
  IF TG_OP = 'DELETE' THEN bid := OLD.buchung_id; ELSE bid := NEW.buchung_id; END IF;
  SELECT b.status, p.gesperrt INTO st, gesperrt
    FROM fibu_buchung b JOIN fibu_periode p ON p.id = b.periode_id WHERE b.id = bid;
  IF st = 'FESTGESCHRIEBEN' THEN
    RAISE EXCEPTION 'GoBD: Zeilen festgeschriebener Buchungen sind unveränderbar';
  END IF;
  IF gesperrt THEN
    RAISE EXCEPTION 'Periodensperre: Buchungszeilen in gesperrter Periode sind unveränderbar';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fibu_zeile_gobd ON fibu_buchungszeile;
CREATE TRIGGER fibu_zeile_gobd BEFORE INSERT OR UPDATE OR DELETE ON fibu_buchungszeile
  FOR EACH ROW EXECUTE FUNCTION fibu_zeile_gobd_schutz();
"""

SQL_AUS = """
DROP TRIGGER IF EXISTS fibu_buchung_gobd ON fibu_buchung;
DROP TRIGGER IF EXISTS fibu_zeile_gobd ON fibu_buchungszeile;
DROP FUNCTION IF EXISTS fibu_buchung_gobd_schutz();
DROP FUNCTION IF EXISTS fibu_zeile_gobd_schutz();
"""


def _ausfuehren(sql):
    def run(apps, schema_editor):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(sql, params=None)

    return run


class Migration(migrations.Migration):
    dependencies = [("fibu", "0001_initial")]
    operations = [migrations.RunPython(_ausfuehren(SQL_AN), _ausfuehren(SQL_AUS))]
