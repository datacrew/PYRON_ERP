"""Unveränderliches Lagerjournal auf Datenbankebene (PostgreSQL).

Lagerbewegungen dürfen weder geändert noch gelöscht werden – auch nicht per direktem SQL.
Korrekturen erfolgen ausschließlich über Gegenbuchungen.
"""
from django.db import migrations

SQL_AN = r"""
CREATE OR REPLACE FUNCTION lager_bewegung_schutz() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Lagerjournal: Bewegung % ist unveränderlich (% nicht erlaubt) – bitte Gegenbuchung erfassen',
    OLD.id, TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lager_bewegung_unveraenderlich ON lager_lagerbewegung;
CREATE TRIGGER lager_bewegung_unveraenderlich BEFORE UPDATE OR DELETE ON lager_lagerbewegung
  FOR EACH ROW EXECUTE FUNCTION lager_bewegung_schutz();
"""

SQL_AUS = """
DROP TRIGGER IF EXISTS lager_bewegung_unveraenderlich ON lager_lagerbewegung;
DROP FUNCTION IF EXISTS lager_bewegung_schutz();
"""


def _ausfuehren(sql):
    def run(apps, schema_editor):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(sql, params=None)

    return run


class Migration(migrations.Migration):
    dependencies = [("lager", "0001_initial")]
    operations = [migrations.RunPython(_ausfuehren(SQL_AN), _ausfuehren(SQL_AUS))]
