"""GoBD-Schutz des DMS auf Datenbankebene (PostgreSQL) – dritte Verteidigungslinie nach Code und Oberfläche.

* dms_dokument: archivierte Dokumente sind unlöschbar; Archivierung ist nicht umkehrbar; Nummer, Art, OCR-Text,
  Archivierungs- und Eingangszeitpunkt sind eingefroren; die Aufbewahrungsfrist kann nur verlängert werden.
* dms_dokumentversion: Versionen sind unveränderbar (kein UPDATE); Löschen nur, solange das Dokument nicht
  archiviert ist.
* dms_dokumentprotokoll: reines Anfügeprotokoll (kein UPDATE); Löschen nur zusammen mit einem nicht
  archivierten Dokument.
"""
from django.db import migrations

SQL_AN = r"""
CREATE OR REPLACE FUNCTION dms_dokument_gobd_schutz() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.archiviert THEN
      RAISE EXCEPTION 'GoBD: Archiviertes Dokument % darf nicht gelöscht werden', OLD.nummer;
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.archiviert THEN
    IF NOT NEW.archiviert THEN
      RAISE EXCEPTION 'GoBD: Archivierung von Dokument % ist nicht umkehrbar', OLD.nummer;
    END IF;
    IF NEW.nummer IS DISTINCT FROM OLD.nummer OR NEW.art IS DISTINCT FROM OLD.art
       OR NEW.ocr_text IS DISTINCT FROM OLD.ocr_text OR NEW.archiviert_am IS DISTINCT FROM OLD.archiviert_am
       OR NEW.erstellt_am IS DISTINCT FROM OLD.erstellt_am THEN
      RAISE EXCEPTION 'GoBD: Archiviertes Dokument % – Nummer, Art, Volltext und Zeitstempel sind unveränderbar',
        OLD.nummer;
    END IF;
    IF OLD.aufbewahren_bis IS NOT NULL AND (NEW.aufbewahren_bis IS NULL OR NEW.aufbewahren_bis < OLD.aufbewahren_bis) THEN
      RAISE EXCEPTION 'GoBD: Aufbewahrungsfrist von Dokument % kann nur verlängert werden', OLD.nummer;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS dms_dokument_gobd ON dms_dokument;
CREATE TRIGGER dms_dokument_gobd BEFORE UPDATE OR DELETE ON dms_dokument
  FOR EACH ROW EXECUTE FUNCTION dms_dokument_gobd_schutz();

CREATE OR REPLACE FUNCTION dms_anhang_gobd_schutz() RETURNS trigger AS $$
DECLARE arch boolean;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'GoBD: % ist unveränderbar (nur Anfügen erlaubt)', TG_TABLE_NAME;
  END IF;
  SELECT d.archiviert INTO arch FROM dms_dokument d WHERE d.id = OLD.dokument_id;
  IF arch THEN
    RAISE EXCEPTION 'GoBD: % eines archivierten Dokuments darf nicht gelöscht werden', TG_TABLE_NAME;
  END IF;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS dms_version_gobd ON dms_dokumentversion;
CREATE TRIGGER dms_version_gobd BEFORE UPDATE OR DELETE ON dms_dokumentversion
  FOR EACH ROW EXECUTE FUNCTION dms_anhang_gobd_schutz();

DROP TRIGGER IF EXISTS dms_protokoll_gobd ON dms_dokumentprotokoll;
CREATE TRIGGER dms_protokoll_gobd BEFORE UPDATE OR DELETE ON dms_dokumentprotokoll
  FOR EACH ROW EXECUTE FUNCTION dms_anhang_gobd_schutz();
"""

SQL_AUS = """
DROP TRIGGER IF EXISTS dms_dokument_gobd ON dms_dokument;
DROP TRIGGER IF EXISTS dms_version_gobd ON dms_dokumentversion;
DROP TRIGGER IF EXISTS dms_protokoll_gobd ON dms_dokumentprotokoll;
DROP FUNCTION IF EXISTS dms_dokument_gobd_schutz();
DROP FUNCTION IF EXISTS dms_anhang_gobd_schutz();
"""


def _ausfuehren(sql):
    def run(apps, schema_editor):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(sql, params=None)

    return run


class Migration(migrations.Migration):
    dependencies = [("dms", "0001_initial")]
    operations = [migrations.RunPython(_ausfuehren(SQL_AN), _ausfuehren(SQL_AUS))]
