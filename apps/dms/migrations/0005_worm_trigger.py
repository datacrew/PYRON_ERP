"""WORM-Manifest auf Datenbankebene als reines Anfügeprotokoll: UPDATE und DELETE sind verboten."""
from django.db import migrations

SQL_AN = r"""
CREATE OR REPLACE FUNCTION dms_worm_schutz() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'WORM: Manifest ist append-only (% verboten)', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS dms_worm_append_only ON dms_wormeintrag;
CREATE TRIGGER dms_worm_append_only BEFORE UPDATE OR DELETE ON dms_wormeintrag
  FOR EACH ROW EXECUTE FUNCTION dms_worm_schutz();
"""
SQL_AB = r"""
DROP TRIGGER IF EXISTS dms_worm_append_only ON dms_wormeintrag;
DROP FUNCTION IF EXISTS dms_worm_schutz();
"""


class Migration(migrations.Migration):
    dependencies = [("dms", "0004_worm_manifest")]
    operations = [migrations.RunSQL(SQL_AN, SQL_AB)]
