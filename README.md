# ERP-System Mittelstand – Phase 1 (Fundament)

**Status: nur das Gerüst steht, die Umsetzung ist NICHT fertig.**

Bisher erledigt:
- Verzeichnisstruktur für den modularen Monolithen (Django-Apps: core, stammdaten, belege, einkauf,
  wareneingang, produktion, vertrieb, versand, fibu, reporting)
- Umgebung geprüft: Python 3.11, Django 5.2, DRF 3.18, WeasyPrint 70 (PDF-Test erfolgreich),
  PostgreSQL lokal läuft (Datenbank `erp`, Benutzer `erp`)

Noch offen (geplante Architektur):
1. core: Mandant, Modul-Registry (an-/abschaltbar), Rechte-Matrix je Modul × Aktion
   (lesen/anlegen/ändern/freigeben/löschen), lückenlose Nummernkreise (select_for_update),
   Audit-Log, Events über Signals
2. stammdaten: Artikel, Geschäftspartner, Stücklisten, Arbeitspläne, SKR03/SKR04, Steuersätze
3. belege: Kopf/Positionen, Workflow Entwurf → Freigegeben → Festgeschrieben → Storniert,
   GoBD-Sperre (Hash-Kette + PostgreSQL-Trigger), PDF-Archiv
4. PDF über Django-Templates + WeasyPrint, DIN-5008-Vorlage, editierbar im Admin
5. Verwaltungsoberfläche, Dashboard, REST-API, Demodaten, Tests, Docker Compose, Doku
