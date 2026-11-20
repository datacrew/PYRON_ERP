# Datenmodell – Phasen 1 bis 3

Stand: Phase 3 (Produktion). Lager, Einkauf und Wareneingang siehe Abschnitt [Phase 2](#phase-2--lager-einkauf-wareneingang), Fertigung/BDE/Akkord siehe [Phase 3](#phase-3--produktion-bde-akkord). Tabellennamen folgen dem Django-Schema `<app>_<modell>`
(z. B. `belege_beleg`). Primärschlüssel ist jeweils `id` (bigint). Geldbeträge sind `numeric` (Decimal),
nie Gleitkomma.

## Übersicht

```mermaid
erDiagram
    USER ||--o{ ROLLE_BENUTZER : "hat"
    ROLLE ||--o{ ROLLE_BENUTZER : "umfasst"
    ROLLE ||--o{ RECHT : "besitzt"
    MODUL ||--o{ RECHT : "wird geregelt durch"
    USER ||--o{ AUDITLOG : "verursacht"

    EINHEIT ||--o{ ARTIKEL : "Mengeneinheit"
    STEUERSATZ ||--o{ ARTIKEL : "Umsatzsteuer"
    ZAHLUNGSBEDINGUNG ||--o{ GESCHAEFTSPARTNER : "Standard"
    GESCHAEFTSPARTNER ||--o{ ADRESSE : "hat"
    GESCHAEFTSPARTNER ||--o{ ANSPRECHPARTNER : "hat"
    ARTIKEL ||--o{ STUECKLISTE : "wird gefertigt nach"
    STUECKLISTE ||--o{ STUECKLISTENPOSITION : "enthält"
    ARTIKEL ||--o{ STUECKLISTENPOSITION : "ist Komponente in"
    ARTIKEL ||--o{ ARBEITSPLAN : "wird gefertigt mit"
    ARBEITSPLAN ||--o{ ARBEITSGANG : "besteht aus"

    GESCHAEFTSPARTNER ||--o{ BELEG : "Empfänger/Absender"
    ZAHLUNGSBEDINGUNG ||--o{ BELEG : "gilt für"
    BELEG ||--o{ BELEGPOSITION : "enthält"
    ARTIKEL ||--o{ BELEGPOSITION : "optional"
    USER ||--o{ BELEG : "erstellt/freigegeben/festgeschrieben/storniert"
    BELEG |o--o| BELEG : "Vorgänger in Hash-Kette (über Prüfsumme)"

    MANDANT {
        string name
        string strasse
        string plz
        string ort
        string ust_id
        string steuernummer
        string handelsregister
        string iban
        string bic
        string kontenrahmen "SKR03 oder SKR04"
    }
    MODUL {
        slug code UK
        string name
        bool aktiv
        bool pflicht
        int reihenfolge
        string url_name
        json geplant
    }
    ROLLE {
        string name UK
        text beschreibung
    }
    RECHT {
        fk rolle
        fk modul
        bool lesen
        bool anlegen
        bool aendern
        bool freigeben
        bool loeschen
    }
    NUMMERNKREIS {
        slug code UK "z. B. BELEG_RECHNUNG"
        string praefix
        bool jahresbezogen
        int stellen
        int naechste_nummer
        int aktuelles_jahr
    }
    AUDITLOG {
        datetime zeitpunkt
        fk benutzer
        string aktion "anlegen/aendern/loeschen/zuordnung"
        string app_label
        string modell
        string objekt_id
        json aenderungen "Feld: alt -> neu"
    }
    ARTIKEL {
        string artikelnummer UK
        string bezeichnung
        string art "ROHSTOFF/HALBZEUG/WERKZEUG/BETRIEBSMITTEL/FERTIGARTIKEL/HANDELSWARE"
        fk einheit
        bool chargenpflicht
        fk steuersatz
        decimal einkaufspreis
        decimal verkaufspreis
        decimal gewicht_kg
        string ean
        string zolltarifnummer
    }
    GESCHAEFTSPARTNER {
        string nummer UK "aus Nummernkreis PARTNER"
        string name
        bool ist_kunde
        bool ist_lieferant
        string ust_id
        string leitweg_id
        string gln
        fk zahlungsbedingung
        string debitorenkonto
        string kreditorenkonto
    }
    ADRESSE {
        fk partner
        string typ "HAUPT/RECHNUNG/LIEFER"
        string strasse
        string plz
        string ort
        string land
    }
    ANSPRECHPARTNER {
        fk partner
        string vorname
        string nachname
        string funktion
        string email
    }
    STUECKLISTE {
        fk artikel
        int version
        decimal basismenge
        bool aktiv
    }
    STUECKLISTENPOSITION {
        fk stueckliste
        int position
        fk komponente
        decimal menge
    }
    ARBEITSPLAN {
        fk artikel
        int version
        bool aktiv
    }
    ARBEITSGANG {
        fk arbeitsplan
        int position
        string bezeichnung
        fk arbeitsplatz "seit Phase 3"
        decimal ruestzeit_min
        decimal stueckzeit_min
        string lohngruppe
        string lohnart "AKKORD | ZEITLOHN (ersetzt akkordfaehig)"
    }
    KONTO {
        string kontenrahmen "SKR03/SKR04"
        string nummer
        string bezeichnung
        string kontoart
    }
    STEUERSATZ {
        string code UK
        decimal satz
    }
    ZAHLUNGSBEDINGUNG {
        string code UK
        int tage_netto
        int skonto_tage
        decimal skonto_prozent
    }
    BELEGVORLAGE {
        string name
        string belegart "leer = Standard"
        text html "Django-Template"
        bool aktiv
    }
    BELEG {
        string belegart
        string nummer UK "erst ab Freigabe"
        string status "ENTWURF/FREIGEGEBEN/FESTGESCHRIEBEN/STORNIERT"
        fk partner
        json partner_daten "Adress-Snapshot ab Freigabe"
        date belegdatum
        date leistungsdatum
        decimal summe_netto
        decimal summe_steuer
        decimal summe_brutto
        int ketten_index UK "Position in der GoBD-Kette"
        string pruefsumme "SHA-256"
        string vorgaenger_pruefsumme
        file pdf "Archiv-PDF"
        string pdf_pruefsumme
        text storno_grund
    }
    BELEGPOSITION {
        fk beleg
        int position
        fk artikel
        string bezeichnung
        decimal menge
        string einheit
        decimal einzelpreis
        decimal rabatt_prozent
        decimal steuersatz "Wert zum Belegzeitpunkt"
        decimal betrag_netto
    }
```

`MANDANT`, `NUMMERNKREIS`, `KONTO`, `BELEGVORLAGE` haben keine Fremdschlüssel; Nummernkreise werden über
ihren `code` angesprochen (`PARTNER`, `BELEG_<BELEGART>`).

## Wichtige Regeln

| Bereich | Regel | Umsetzung |
|---|---|---|
| Rechte | Rolle × Modul × Aktion, Rechte mehrerer Rollen werden addiert | `core.permissions.rechte_fuer()` |
| Module | Inaktives Modul ist für alle gesperrt | Registry + Rechteprüfung |
| Nummernkreise | lückenlos, transaktionssicher, Jahreswechsel | `SELECT … FOR UPDATE` in `naechste_nummer()` |
| Belegnummer | wird erst bei Freigabe vergeben | `belege.services.freigeben()` |
| Steuersatz in Position | wird als Wert kopiert, nicht referenziert | spätere Satzänderungen verändern alte Belege nicht |
| Partneradresse im Beleg | Snapshot (`partner_daten`) ab Freigabe | Änderungen am Stamm wirken nicht rückwirkend |
| GoBD | festgeschriebene/stornierte Belege unveränderbar | Code-Sperre + PL/pgSQL-Trigger + Hash-Kette |
| Stücklisten | mehrstufig, zyklenfrei | `Stueckliste.aufloesen()` wirft `StuecklistenFehler` bei Zyklus |
| Löschen | Stammdaten mit Verwendung sind geschützt | `on_delete=PROTECT` |

## Beleg-Status

```mermaid
stateDiagram-v2
    [*] --> ENTWURF
    ENTWURF --> FREIGEGEBEN: freigeben (Nummer vergeben, Snapshot)
    FREIGEGEBEN --> FESTGESCHRIEBEN: festschreiben (Prüfsumme, PDF-Archiv)
    FREIGEGEBEN --> STORNIERT: stornieren (Grund)
    FESTGESCHRIEBEN --> STORNIERT: stornieren (Grund)
    ENTWURF --> [*]: löschen
    STORNIERT --> [*]
```

## Hash-Kette (GoBD)

```
pruefsumme(n) = SHA-256( vorgaenger_pruefsumme(n-1) | Kopfdaten | Positionen | Summen | Partner-Snapshot | Festschreibzeitpunkt | Ketten-Index )
```

Jede Manipulation an einem festgeschriebenen Beleg (auch per direktem SQL bei deaktiviertem Trigger) wird
bei der Kettenprüfung (Verwaltung → GoBD-Kettenprüfung) erkannt: Die Prüfsumme des Belegs stimmt dann nicht
mehr und die Verkettung zum Nachfolger bricht.

---

## Phase 2 – Lager, Einkauf, Wareneingang

```mermaid
erDiagram
    LAGERORT ||--o{ LAGERPLATZ : "hat"
    ARTIKEL ||--o| ARTIKELLAGER : "Dispositions-/Bewertungsdaten"
    LAGERORT ||--o{ ARTIKELLAGER : "Standard-Lagerort"
    GESCHAEFTSPARTNER ||--o{ ARTIKELLAGER : "bevorzugter Lieferant"
    ARTIKEL ||--o{ CHARGE : "hat"
    GESCHAEFTSPARTNER ||--o{ CHARGE : "Lieferant"
    ARTIKEL ||--o{ BESTAND : ""
    LAGERORT ||--o{ BESTAND : ""
    LAGERPLATZ ||--o{ BESTAND : "optional"
    CHARGE ||--o{ BESTAND : "optional"
    ARTIKEL ||--o{ LAGERBEWEGUNG : ""
    CHARGE ||--o{ LAGERBEWEGUNG : "optional"
    LAGERORT ||--o{ LAGERBEWEGUNG : "von / nach"
    LAGERORT ||--o{ INVENTUR : ""
    INVENTUR ||--o{ INVENTURPOSITION : "Zählliste"

    GESCHAEFTSPARTNER ||--o{ BESTELLUNG : "Lieferant"
    BESTELLUNG ||--o{ BESTELLPOSITION : "enthält"
    ARTIKEL ||--o{ BESTELLPOSITION : ""
    BESTELLUNG |o--o{ BESTELLUNG : "Anfrage → Folgebestellung"
    LAGERORT ||--o{ BESTELLUNG : "Lieferort"
    ARTIKEL ||--o{ BESTELLANFORDERUNG : ""
    BESTELLPOSITION |o--o{ BESTELLANFORDERUNG : "übernommen in"

    BESTELLUNG ||--o{ WARENEINGANG : "geliefert mit"
    WARENEINGANG ||--o{ WEPOSITION : "enthält"
    BESTELLPOSITION ||--o{ WEPOSITION : "beliefert"
    CHARGE |o--o{ WEPOSITION : "erfasst"
    LAGERBEWEGUNG |o--o| WEPOSITION : "Zugangsbuchung"

    ARTIKELLAGER {
        decimal mindestbestand
        decimal meldebestand
        decimal bestellmenge
        int wiederbeschaffungszeit_tage
        bool qs_pflicht
        decimal durchschnittspreis "gleitend, nur per Buchung"
        decimal bestand_gesamt "inkl. Sperrbestand"
    }
    CHARGE {
        string chargennummer "UK je Artikel"
        string lieferantencharge
        date herstelldatum
        date mhd
    }
    BESTAND {
        string bestandsart "FREI | GESPERRT"
        decimal menge "CHECK >= 0"
    }
    LAGERBEWEGUNG {
        string art "ZUGANG | ABGANG | UMBUCHUNG | INVENTUR"
        decimal menge
        string von_bestandsart
        string nach_bestandsart
        decimal bewertungspreis
        decimal wert
        decimal durchschnittspreis_neu
        decimal bestand_neu
        string quelle "manuell | wareneingang | qs | inventur | anfangsbestand"
        string beleg_nummer
    }
    INVENTUR {
        string nummer UK "INV-"
        date stichtag
        string status "OFFEN | GEBUCHT"
    }
    INVENTURPOSITION {
        decimal sollmenge
        decimal istmenge
        decimal differenz
    }
    BESTELLUNG {
        string typ "ANFRAGE | BESTELLUNG"
        string nummer "EA- / EK-, ab Freigabe"
        string status "ENTWURF | ANGEFRAGT | BESTELLT | TEILGELIEFERT | GELIEFERT | STORNIERT"
        json partner_daten "Snapshot ab Freigabe"
        date liefertermin
        decimal summe_netto
        string storno_grund
    }
    BESTELLPOSITION {
        decimal menge
        decimal einzelpreis
        decimal rabatt_prozent
        decimal steuersatz "Wert kopiert"
        date liefertermin
        decimal geliefert_menge "nur durch Wareneingang"
        bool erledigt "Restmenge abgeschlossen"
    }
    BESTELLANFORDERUNG {
        string nummer UK "BA-"
        decimal menge
        date bedarfstermin
        string status "OFFEN | BESTELLT | ABGELEHNT"
    }
    WARENEINGANG {
        string nummer UK "WEB-"
        string lieferschein_nr
        date datum
        text hinweise "Differenzprotokoll"
    }
    WEPOSITION {
        decimal menge
        decimal offen_vorher
        decimal mengendifferenz "+ Über-, - Unterlieferung"
        decimal bestellpreis
        decimal lieferpreis
        decimal preisdifferenz
        string qs_status "OFFEN | FREIGEGEBEN | GESPERRT"
        bool zuruckgesendet
    }
```

### Regeln Phase 2

| Bereich | Regel | Umsetzung |
|---|---|---|
| Bestandsänderung | nur über eine Funktion, mit Journal | `lager.services.buchen()`; Sperre `SELECT … FOR UPDATE` auf `ArtikelLager` |
| Journal | unveränderlich, Korrektur nur per Gegenbuchung | `Lagerbewegung.save()/delete()` + Trigger `lager_bewegung_unveraenderlich` |
| Bestand | nie negativ, eindeutig je Artikel/Ort/Platz/Charge/Bestandsart | Check-Constraint, Unique-Constraint (`NULLS NOT DISTINCT`) |
| Bewertung | gleitender Durchschnitt; Abgang/Inventur-Minus zum Ø; Umbuchung ohne Wert | `buchen()` |
| Chargenpflicht | keine Buchung/kein Wareneingang ohne Charge | `buchen()`, `wareneingang.services.buchen()` |
| QS | QS-pflichtige Zugänge in Sperrbestand; Freigabe = Umbuchung GESPERRT → FREI | `wareneingang.services.qs_entscheiden()` |
| Nummern | BA-, EA-, EK- (bei Freigabe), WEB-, INV- | `naechste_nummer()` |
| Bestellstatus | aus gelieferten/erledigten Positionen abgeleitet | `einkauf.services.status_fortschreiben()` |
| Fibu-Kopplung | nur Event `wareneingang.gebucht` (nach Commit) | Abonnent in `apps/fibu/ereignisse.py` (Logging) |

### Bestellstatus

```mermaid
stateDiagram-v2
    [*] --> ENTWURF
    ENTWURF --> BESTELLT: freigeben (Bestellung, Nummer EK-)
    ENTWURF --> ANGEFRAGT: freigeben (Anfrage, Nummer EA-)
    BESTELLT --> TEILGELIEFERT: Wareneingang (Teilmenge)
    TEILGELIEFERT --> GELIEFERT: Restlieferung / Restmenge abgeschlossen
    BESTELLT --> GELIEFERT: Wareneingang (vollständig)
    TEILGELIEFERT --> BESTELLT: Rücksendung der gesamten Liefermenge
    ENTWURF --> STORNIERT: stornieren (Grund)
    BESTELLT --> STORNIERT: stornieren (Grund, nur ohne Lieferung)
    ANGEFRAGT --> STORNIERT: stornieren (Grund)
    ENTWURF --> [*]: löschen
```


## Phase 3 – Produktion, BDE, Akkord

Neue Stammdaten in `apps/stammdaten` (`Arbeitsplatz`, `Mitarbeiter`, Auswahl `Lohnart`), Bewegungsdaten in
`apps/produktion`. Migration `stammdaten/0002` übernimmt den bisherigen Text-Arbeitsplatz und `akkordfaehig`
in die neuen Felder.

```mermaid
erDiagram
    ARBEITSPLATZ ||--o{ ARBEITSGANG : "Plan"
    ARBEITSPLATZ ||--o{ FA_ARBEITSGANG : "Auftrag"
    ARTIKEL ||--o{ FERTIGUNGSAUFTRAG : fertigt
    STUECKLISTE ||--o{ FERTIGUNGSAUFTRAG : "Version"
    ARBEITSPLAN ||--o{ FERTIGUNGSAUFTRAG : "Version"
    FERTIGUNGSAUFTRAG ||--o{ FERTIGUNGSAUFTRAG : "Unterauftrag (uebergeordnet)"
    FERTIGUNGSAUFTRAG ||--o{ FA_MATERIAL : Bedarf
    FERTIGUNGSAUFTRAG ||--o{ FA_ARBEITSGANG : Arbeitsgaenge
    FA_MATERIAL o|--o| FERTIGUNGSAUFTRAG : "unterauftrag"
    FA_MATERIAL ||--o{ MATERIALENTNAHME : entnommen
    MATERIALENTNAHME ||--|| LAGERBEWEGUNG : "ABGANG"
    FERTIGUNGSAUFTRAG ||--o{ FERTIGMELDUNG : gemeldet
    FERTIGMELDUNG ||--|| LAGERBEWEGUNG : "ZUGANG"
    FERTIGMELDUNG }o--|| CHARGE : "Fertigungscharge"
    MITARBEITER ||--o{ ANWESENHEIT : "Kommen/Gehen"
    MITARBEITER ||--o{ STEMPELUNG : stempelt
    FA_ARBEITSGANG ||--o{ STEMPELUNG : Zeiten
    FA_ARBEITSGANG ||--o{ RUECKMELDUNG : Mengen
    STEMPELUNG |o--o{ RUECKMELDUNG : "beim Abstempeln"
    MITARBEITER }o--o| USER : "optional"

    ARBEITSPLATZ {
        string code UK
        string bezeichnung
        string art "MASCHINE | HAND"
        string kostenstelle
        decimal stundensatz "EUR/h"
    }
    MITARBEITER {
        string personalnummer UK
        string ausweisnummer "Stempelkarte, eindeutig wenn gesetzt"
        string vorname
        string nachname
        string lohngruppe
        decimal stundenlohn "Grundlohn EUR/h"
        decimal akkordrichtsatz "EUR/h"
        fk benutzer "optional"
    }
    FERTIGUNGSAUFTRAG {
        string nummer UK "FA-JJJJ-NNNNN"
        fk artikel
        fk stueckliste
        fk arbeitsplan
        decimal menge
        date starttermin
        date termin
        string status
        fk uebergeordnet
        fk ziel_lagerort
        decimal gut_menge
        decimal ausschuss_menge
        datetime freigegeben_am
        string storno_grund
    }
    FA_MATERIAL {
        fk auftrag
        int position
        fk artikel
        decimal soll_menge
        decimal entnommen_menge
        fk unterauftrag
    }
    FA_ARBEITSGANG {
        fk auftrag
        int position "Code = Auftragsnr-Position"
        string bezeichnung
        fk arbeitsplatz
        decimal ruestzeit_min
        decimal stueckzeit_min
        string lohngruppe
        string lohnart
        string status "OFFEN | LAEUFT | UNTERBROCHEN | ERLEDIGT"
        decimal gut_menge
        decimal ausschuss_menge
        decimal nacharbeit_menge
    }
    ANWESENHEIT {
        fk mitarbeiter
        datetime kommen
        datetime gehen
    }
    STEMPELUNG {
        fk mitarbeiter
        fk arbeitsgang
        string taetigkeit "RUESTEN | FERTIGEN"
        datetime beginn
        datetime ende
        string ende_art "UNTERBRECHUNG | ENDE"
        string quelle "TERMINAL | API | MANUELL"
        bool korrigiert
        string korrektur_grund
    }
    RUECKMELDUNG {
        fk arbeitsgang
        fk mitarbeiter
        fk stempelung
        datetime zeitpunkt
        decimal gut_menge
        decimal ausschuss_menge
        string ausschuss_grund
        decimal nacharbeit_menge
    }
    MATERIALENTNAHME {
        fk auftrag
        fk material
        fk artikel
        fk charge
        fk lagerort
        decimal menge
        decimal wert
        fk bewegung
    }
    FERTIGMELDUNG {
        fk auftrag
        decimal menge
        fk charge
        fk lagerort
        decimal materialkosten
        decimal lohnkosten
        decimal platzkosten
        decimal herstellkosten_stueck
        fk bewegung
    }
```

### Regeln Phase 3

| Bereich | Regel | Umsetzung |
|---|---|---|
| Nummern | `FA-` (Anlage), Fertigungscharge `FC-` (Fertigmeldung ohne eigene Nummer) | `naechste_nummer()` |
| Freigabe | kopiert Stücklisten-Bedarf (eine Ebene, × Menge) und Arbeitsgänge; optional Unteraufträge für fehlende Eigenfertigungsteile | `produktion.services.freigeben()` |
| Fehlteile | Bedarf gegen **freien** Bestand, mehrstufig für Eigenfertigungsteile | `fehlteile()` |
| Stempeln | höchstens eine offene Stempelung und eine offene Anwesenheit je Mitarbeiter, keine Überschneidung, Ende ≥ Beginn | Service-Prüfung + Partial-Unique- und Check-Constraints |
| Korrektur | nur mit Recht `bde.ändern`, Grund Pflicht, Vorher/Nachher im Audit-Log | `stempelung_korrigieren()`, `nachtragen()` |
| Rückmeldung | Ausschuss nur mit Grund; Mengen ≥ 0 | `rueckmelden()` |
| Lager | Entnahme = `ABGANG`, Fertigmeldung = `ZUGANG` ausschließlich über `lager.services.buchen()` (Journal-Trigger bleibt wirksam) | `material_entnehmen()`, `fertigmelden()` |
| Bewertung | Herstellkosten = Material (Entnahmewerte) + Lohn (Ist-Zeit × Richtsatz/Stundenlohn) + Arbeitsplatz (Ist-Zeit × Stundensatz) | `ist_kosten()`, `fertigmelden()` |
| Akkord | Vorgabe = Σ Gut × te + tr; Leistungsgrad = Vorgabe/Ist; Verdienst = Vorgabe × Richtsatz/60; Garantie = Ist × max(Stundenlohn, Mindestlohn)/60 | `akkord_auswertung()` |
| Fibu-Kopplung | nur Events `produktion.material_entnommen`, `produktion.fertiggemeldet` | Abonnent in `apps/fibu/ereignisse.py` (Logging) |

### Status Fertigungsauftrag

```mermaid
stateDiagram-v2
    [*] --> ENTWURF
    ENTWURF --> FREIGEGEBEN: freigeben (Material + Arbeitsgänge kopieren)
    FREIGEGEBEN --> IN_ARBEIT: erste Entnahme / Anstempelung
    FREIGEGEBEN --> TEILGEFERTIGT: erste Rückmeldung
    IN_ARBEIT --> TEILGEFERTIGT: Rückmeldung oder Teil-Fertigmeldung
    TEILGEFERTIGT --> ABGESCHLOSSEN: Restmenge gemeldet oder „abschließen“
    IN_ARBEIT --> ABGESCHLOSSEN: Fertigmeldung der gesamten Menge
    ENTWURF --> STORNIERT: stornieren (Grund)
    FREIGEGEBEN --> STORNIERT: stornieren (ohne Entnahme/Fertigmeldung, niemand angestempelt)
    IN_ARBEIT --> STORNIERT: dto.
    ENTWURF --> [*]: löschen
```
