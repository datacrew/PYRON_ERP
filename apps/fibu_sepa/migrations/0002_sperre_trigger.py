"""Sperre exportierter Zahlungsläufe auf Datenbankebene (PostgreSQL), analog zu den GoBD-Triggern der Fibu.

* Exportierte Läufe (exportiert_am gesetzt) sind unlöschbar; die eingefrorenen Kopf-Spalten
  (Zahlungslauf.EINGEFROREN) können auch per direktem SQL nicht mehr geändert werden.
* Posten exportierter Läufe: kein INSERT/DELETE, eingefrorene Spalten (ZahlungslaufPosten.EINGEFROREN)
  unveränderbar. Erlaubt bleiben Status, Storno-, Abgleich- und Rücklastschrift-Verweise.
"""
from django.db import migrations

LAUF_SPALTEN = ("nummer", "art", "bankkonto_id", "ausfuehrungsdatum", "skonto_nutzen", "sammelbuchung", "msg_id",
                "summe", "anzahl", "xml_inhalt", "xml_dateiname", "xml_sha256", "xsd_geprueft", "buchung_id",
                "freigegeben_von_id", "freigegeben_am", "exportiert_von_id", "exportiert_am", "erstellt_von_id",
                "erstellt_am")
POSTEN_SPALTEN = ("lauf_id", "op_id", "partner_id", "mandat_id", "ausgewaehlt", "name", "iban", "bic", "offen",
                  "skonto", "betrag", "verwendungszweck", "end_to_end_id", "pmtinf_id", "sequenz", "mandatsreferenz",
                  "mandat_datum", "lastschrift_typ")


def _row(prefix, spalten):
    return "ROW(" + ", ".join(f"{prefix}.{s}" for s in spalten) + ")"


SQL_AN = f"""
CREATE OR REPLACE FUNCTION fibu_sepa_lauf_schutz() RETURNS trigger AS $$
BEGIN
  IF OLD.exportiert_am IS NOT NULL THEN
    IF TG_OP = 'DELETE' THEN
      RAISE EXCEPTION 'SEPA: Exportierter Zahlungslauf % ist unlöschbar (Korrektur nur per Storno)', OLD.nummer;
    END IF;
    IF {_row("NEW", LAUF_SPALTEN)} IS DISTINCT FROM {_row("OLD", LAUF_SPALTEN)} THEN
      RAISE EXCEPTION 'SEPA: Exportierter Zahlungslauf % ist unveränderbar', OLD.nummer;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fibu_sepa_lauf_gesperrt ON fibu_sepa_zahlungslauf;
CREATE TRIGGER fibu_sepa_lauf_gesperrt BEFORE UPDATE OR DELETE ON fibu_sepa_zahlungslauf
  FOR EACH ROW EXECUTE FUNCTION fibu_sepa_lauf_schutz();

CREATE OR REPLACE FUNCTION fibu_sepa_posten_schutz() RETURNS trigger AS $$
DECLARE exportiert boolean;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT l.exportiert_am IS NOT NULL INTO exportiert FROM fibu_sepa_zahlungslauf l WHERE l.id = OLD.lauf_id;
    IF exportiert AND (TG_OP = 'DELETE' OR {_row("NEW", POSTEN_SPALTEN)} IS DISTINCT FROM {_row("OLD", POSTEN_SPALTEN)}) THEN
      RAISE EXCEPTION 'SEPA: Posten exportierter Zahlungsläufe sind unveränderbar';
    END IF;
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    SELECT l.exportiert_am IS NOT NULL INTO exportiert FROM fibu_sepa_zahlungslauf l WHERE l.id = NEW.lauf_id;
    IF exportiert AND (TG_OP = 'INSERT' OR NEW.lauf_id IS DISTINCT FROM OLD.lauf_id) THEN
      RAISE EXCEPTION 'SEPA: Exportierten Zahlungsläufen können keine Posten hinzugefügt werden';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fibu_sepa_posten_gesperrt ON fibu_sepa_zahlungslaufposten;
CREATE TRIGGER fibu_sepa_posten_gesperrt BEFORE INSERT OR UPDATE OR DELETE ON fibu_sepa_zahlungslaufposten
  FOR EACH ROW EXECUTE FUNCTION fibu_sepa_posten_schutz();
"""

SQL_AUS = """
DROP TRIGGER IF EXISTS fibu_sepa_lauf_gesperrt ON fibu_sepa_zahlungslauf;
DROP TRIGGER IF EXISTS fibu_sepa_posten_gesperrt ON fibu_sepa_zahlungslaufposten;
DROP FUNCTION IF EXISTS fibu_sepa_lauf_schutz();
DROP FUNCTION IF EXISTS fibu_sepa_posten_schutz();
"""


def _ausfuehren(sql):
    def run(apps, schema_editor):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(sql, params=None)

    return run


class Migration(migrations.Migration):
    dependencies = [("fibu_sepa", "0001_initial")]
    operations = [migrations.RunPython(_ausfuehren(SQL_AN), _ausfuehren(SQL_AUS))]
