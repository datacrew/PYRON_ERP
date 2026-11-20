"""Grundbestand SKR03/SKR04 inkl. Bilanz-/GuV-Zuordnung, Automatikfunktionen und Steuer-Automatik,
dazu Steuerschlüssel, Kontierungsregeln, Mahnstufen und Basiszinssätze.

WICHTIG: Praxisüblicher Grundbestand angelehnt an die DATEV-Kontenrahmen – vor dem Produktivbetrieb
mit der Steuerberatung abstimmen (insbesondere die frei gewählten Verrechnungskonten WE/RE und
„ungeklärte Bankumsätze“ sowie die UStVA-Kennziffern und DATEV-BU-Schlüssel).

Tupel: (Nummer, Bezeichnung, Kontoart, Bilanzposition, GuV-Position, Automatik, Steuer-Automatik)
"""
from decimal import Decimal

W = "B.II.4|P.C.8"  # Wechselkonto: Sollsaldo → sonstige VG, Habensaldo → sonstige Verbindlichkeiten

SKR03 = [
    ("0027", "EDV-Software", "aktiv", "A.I", "", "", ""),
    ("0200", "Technische Anlagen und Maschinen", "aktiv", "A.II", "", "", ""),
    ("0210", "Maschinen", "aktiv", "A.II", "", "", ""),
    ("0320", "Pkw", "aktiv", "A.II", "", "", ""),
    ("0400", "Betriebsausstattung", "aktiv", "A.II", "", "", ""),
    ("0420", "Büroeinrichtung", "aktiv", "A.II", "", "", ""),
    ("0480", "Geringwertige Wirtschaftsgüter", "aktiv", "A.II", "", "", ""),
    ("0525", "Wertpapiere des Anlagevermögens", "aktiv", "A.III", "", "", ""),
    ("0630", "Verbindlichkeiten gegenüber Kreditinstituten", "passiv", "P.C.2", "", "", ""),
    ("0800", "Gezeichnetes Kapital", "passiv", "P.A.I", "", "", ""),
    ("0840", "Kapitalrücklage", "passiv", "P.A.II", "", "", ""),
    ("0860", "Gewinnvortrag vor Verwendung", "passiv", "P.A.IV", "", "GEWINNVORTRAG", ""),
    ("0868", "Verlustvortrag vor Verwendung", "passiv", "P.A.IV", "", "", ""),
    ("0955", "Steuerrückstellungen", "passiv", "P.B", "", "", ""),
    ("0970", "Sonstige Rückstellungen", "passiv", "P.B", "", "", ""),
    ("0977", "Rückstellungen für Abschluss- und Prüfungskosten", "passiv", "P.B", "", "", ""),
    ("0980", "Aktive Rechnungsabgrenzung", "aktiv", "C", "", "", ""),
    ("0990", "Passive Rechnungsabgrenzung", "passiv", "P.D", "", "", ""),
    ("1000", "Kasse", "aktiv", "B.IV", "", "", ""),
    ("1200", "Bank", "aktiv", "B.IV", "", "", ""),
    ("1360", "Geldtransit", "aktiv", "B.IV", "", "", ""),
    ("1400", "Forderungen aus Lieferungen und Leistungen", "aktiv", "B.II.1", "", "SAMMEL_DEB", ""),
    ("1500", "Sonstige Vermögensgegenstände", "aktiv", "B.II.4", "", "", ""),
    ("1571", "Abziehbare Vorsteuer 7 %", "steuer", "B.II.4", "", "", ""),
    ("1572", "Abziehbare Vorsteuer aus innergemeinschaftlichem Erwerb 7 %", "steuer", "B.II.4", "", "", ""),
    ("1574", "Abziehbare Vorsteuer aus innergemeinschaftlichem Erwerb 19 %", "steuer", "B.II.4", "", "", ""),
    ("1576", "Abziehbare Vorsteuer 19 %", "steuer", "B.II.4", "", "", ""),
    ("1577", "Abziehbare Vorsteuer nach § 13b UStG 19 %", "steuer", "B.II.4", "", "", ""),
    ("1590", "Durchlaufende Posten", "aktiv", W, "", "", ""),
    ("1595", "WE/RE-Verrechnungskonto (Wareneingang/Rechnungseingang)", "aktiv", W, "", "WERE", ""),
    ("1598", "Verrechnungskonto ungeklärte Bankumsätze", "aktiv", W, "", "UNGEKLAERT", ""),
    ("1600", "Verbindlichkeiten aus Lieferungen und Leistungen", "passiv", "P.C.4", "", "SAMMEL_KRED", ""),
    ("1700", "Sonstige Verbindlichkeiten", "passiv", "P.C.8", "", "", ""),
    ("1740", "Verbindlichkeiten aus Lohn und Gehalt", "passiv", "P.C.8", "", "", ""),
    ("1741", "Verbindlichkeiten aus Lohn- und Kirchensteuer", "passiv", "P.C.8", "", "", ""),
    ("1742", "Verbindlichkeiten im Rahmen der sozialen Sicherheit", "passiv", "P.C.8", "", "", ""),
    ("1771", "Umsatzsteuer 7 %", "steuer", "P.C.8", "", "", ""),
    ("1772", "Umsatzsteuer aus innergemeinschaftlichem Erwerb 7 %", "steuer", "P.C.8", "", "", ""),
    ("1774", "Umsatzsteuer aus innergemeinschaftlichem Erwerb 19 %", "steuer", "P.C.8", "", "", ""),
    ("1776", "Umsatzsteuer 19 %", "steuer", "P.C.8", "", "", ""),
    ("1787", "Umsatzsteuer nach § 13b UStG 19 %", "steuer", "P.C.8", "", "", ""),
    ("1780", "Umsatzsteuer-Vorauszahlungen", "steuer", W, "", "", ""),
    ("1790", "Umsatzsteuer Vorjahr", "steuer", W, "", "", ""),
    ("2100", "Zinsen und ähnliche Aufwendungen", "aufwand", "", "13", "", ""),
    ("2200", "Körperschaftsteuer", "aufwand", "", "14", "", ""),
    ("2400", "Forderungsverluste", "aufwand", "", "8", "", ""),
    ("2650", "Sonstige Zinsen und ähnliche Erträge", "ertrag", "", "11", "ZINSERTRAG", ""),
    ("2700", "Sonstige Erträge", "ertrag", "", "4", "MAHNGEBUEHR", ""),
    ("3000", "Roh-, Hilfs- und Betriebsstoffe", "aufwand", "", "5a", "", ""),
    ("3100", "Fremdleistungen", "aufwand", "", "5b", "", ""),
    ("3120", "Bauleistungen § 13b UStG 19 % Vorsteuer und 19 % Umsatzsteuer", "aufwand", "", "5b", "", "RC19"),
    ("3300", "Wareneingang 7 % Vorsteuer", "aufwand", "", "5a", "", "V7"),
    ("3400", "Wareneingang 19 % Vorsteuer", "aufwand", "", "5a", "", "V19"),
    ("3425", "Innergemeinschaftlicher Erwerb 19 % Vorsteuer und 19 % Umsatzsteuer", "aufwand", "", "5a", "",
     "IGE19"),
    ("3731", "Erhaltene Skonti 7 % Vorsteuer", "aufwand", "", "5a", "", "V7"),
    ("3736", "Erhaltene Skonti 19 % Vorsteuer", "aufwand", "", "5a", "", "V19"),
    ("3970", "Bestand Roh-, Hilfs- und Betriebsstoffe", "aktiv", "B.I", "", "", ""),
    ("3980", "Bestand Waren", "aktiv", "B.I", "", "", ""),
    ("4110", "Löhne", "aufwand", "", "6a", "", ""),
    ("4120", "Gehälter", "aufwand", "", "6a", "", ""),
    ("4130", "Gesetzliche soziale Aufwendungen", "aufwand", "", "6b", "", ""),
    ("4138", "Beiträge zur Berufsgenossenschaft", "aufwand", "", "6b", "", ""),
    ("4210", "Miete", "aufwand", "", "8", "", ""),
    ("4240", "Gas, Strom, Wasser", "aufwand", "", "8", "", ""),
    ("4320", "Gewerbesteuer", "aufwand", "", "14", "", ""),
    ("4360", "Versicherungen", "aufwand", "", "8", "", ""),
    ("4380", "Beiträge", "aufwand", "", "8", "", ""),
    ("4500", "Fahrzeugkosten", "aufwand", "", "8", "", ""),
    ("4510", "Kfz-Steuer", "aufwand", "", "16", "", ""),
    ("4600", "Werbekosten", "aufwand", "", "8", "", ""),
    ("4730", "Ausgangsfrachten", "aufwand", "", "8", "", ""),
    ("4800", "Reparaturen und Instandhaltung von technischen Anlagen und Maschinen", "aufwand", "", "8", "", ""),
    ("4830", "Abschreibungen auf Sachanlagen", "aufwand", "", "7", "", ""),
    ("4855", "Sofortabschreibung geringwertiger Wirtschaftsgüter", "aufwand", "", "7", "", ""),
    ("4900", "Sonstige betriebliche Aufwendungen", "aufwand", "", "8", "", ""),
    ("4910", "Porto", "aufwand", "", "8", "", ""),
    ("4920", "Telefon", "aufwand", "", "8", "", ""),
    ("4930", "Bürobedarf", "aufwand", "", "8", "", ""),
    ("4950", "Rechts- und Beratungskosten", "aufwand", "", "8", "", ""),
    ("4955", "Buchführungskosten", "aufwand", "", "8", "", ""),
    ("4957", "Abschluss- und Prüfungskosten", "aufwand", "", "8", "", ""),
    ("4970", "Nebenkosten des Geldverkehrs", "aufwand", "", "8", "GELDVERKEHR", ""),
    ("7000", "Unfertige Erzeugnisse", "aktiv", "B.I", "", "", ""),
    ("7110", "Fertige Erzeugnisse", "aktiv", "B.I", "", "", ""),
    ("8120", "Steuerfreie Umsätze § 4 Nr. 1a UStG (Ausfuhr)", "ertrag", "", "1", "", "AUS"),
    ("8125", "Steuerfreie innergemeinschaftliche Lieferungen § 4 Nr. 1b UStG", "ertrag", "", "1", "", "IGL"),
    ("8300", "Erlöse 7 % USt", "ertrag", "", "1", "", "U7"),
    ("8400", "Erlöse 19 % USt", "ertrag", "", "1", "", "U19"),
    ("8731", "Gewährte Skonti 7 % USt", "ertrag", "", "1", "", "U7"),
    ("8736", "Gewährte Skonti 19 % USt", "ertrag", "", "1", "", "U19"),
    ("8743", "Gewährte Skonti steuerfreie innergemeinschaftliche Lieferungen", "ertrag", "", "1", "", "IGL"),
    ("8960", "Bestandsveränderungen unfertige Erzeugnisse", "ertrag", "", "2", "", ""),
    ("8980", "Bestandsveränderungen fertige Erzeugnisse", "ertrag", "", "2", "", ""),
    ("9000", "Saldenvorträge Sachkonten", "neutral", "", "", "SALDENVORTRAG", ""),
    ("9008", "Saldenvorträge Debitoren", "neutral", "", "", "", ""),
    ("9009", "Saldenvorträge Kreditoren", "neutral", "", "", "", ""),
]

SKR04 = [
    ("0135", "EDV-Software", "aktiv", "A.I", "", "", ""),
    ("0400", "Technische Anlagen und Maschinen", "aktiv", "A.II", "", "", ""),
    ("0440", "Maschinen", "aktiv", "A.II", "", "", ""),
    ("0520", "Pkw", "aktiv", "A.II", "", "", ""),
    ("0650", "Büroeinrichtung", "aktiv", "A.II", "", "", ""),
    ("0670", "Geringwertige Wirtschaftsgüter", "aktiv", "A.II", "", "", ""),
    ("0690", "Sonstige Betriebs- und Geschäftsausstattung", "aktiv", "A.II", "", "", ""),
    ("0900", "Wertpapiere des Anlagevermögens", "aktiv", "A.III", "", "", ""),
    ("1000", "Roh-, Hilfs- und Betriebsstoffe (Bestand)", "aktiv", "B.I", "", "", ""),
    ("1040", "Unfertige Erzeugnisse", "aktiv", "B.I", "", "", ""),
    ("1110", "Fertige Erzeugnisse", "aktiv", "B.I", "", "", ""),
    ("1140", "Waren (Bestand)", "aktiv", "B.I", "", "", ""),
    ("1200", "Forderungen aus Lieferungen und Leistungen", "aktiv", "B.II.1", "", "SAMMEL_DEB", ""),
    ("1300", "Sonstige Vermögensgegenstände", "aktiv", "B.II.4", "", "", ""),
    ("1370", "Durchlaufende Posten", "aktiv", W, "", "", ""),
    ("1401", "Abziehbare Vorsteuer 7 %", "steuer", "B.II.4", "", "", ""),
    ("1402", "Abziehbare Vorsteuer aus innergemeinschaftlichem Erwerb 7 %", "steuer", "B.II.4", "", "", ""),
    ("1404", "Abziehbare Vorsteuer aus innergemeinschaftlichem Erwerb 19 %", "steuer", "B.II.4", "", "", ""),
    ("1406", "Abziehbare Vorsteuer 19 %", "steuer", "B.II.4", "", "", ""),
    ("1407", "Abziehbare Vorsteuer nach § 13b UStG 19 %", "steuer", "B.II.4", "", "", ""),
    ("1460", "Geldtransit", "aktiv", "B.IV", "", "", ""),
    ("1495", "WE/RE-Verrechnungskonto (Wareneingang/Rechnungseingang)", "aktiv", W, "", "WERE", ""),
    ("1498", "Verrechnungskonto ungeklärte Bankumsätze", "aktiv", W, "", "UNGEKLAERT", ""),
    ("1600", "Kasse", "aktiv", "B.IV", "", "", ""),
    ("1800", "Bank", "aktiv", "B.IV", "", "", ""),
    ("1900", "Aktive Rechnungsabgrenzung", "aktiv", "C", "", "", ""),
    ("2900", "Gezeichnetes Kapital", "passiv", "P.A.I", "", "", ""),
    ("2920", "Kapitalrücklage", "passiv", "P.A.II", "", "", ""),
    ("2970", "Gewinnvortrag vor Verwendung", "passiv", "P.A.IV", "", "GEWINNVORTRAG", ""),
    ("2978", "Verlustvortrag vor Verwendung", "passiv", "P.A.IV", "", "", ""),
    ("3020", "Steuerrückstellungen", "passiv", "P.B", "", "", ""),
    ("3070", "Sonstige Rückstellungen", "passiv", "P.B", "", "", ""),
    ("3095", "Rückstellungen für Abschluss- und Prüfungskosten", "passiv", "P.B", "", "", ""),
    ("3150", "Verbindlichkeiten gegenüber Kreditinstituten", "passiv", "P.C.2", "", "", ""),
    ("3300", "Verbindlichkeiten aus Lieferungen und Leistungen", "passiv", "P.C.4", "", "SAMMEL_KRED", ""),
    ("3500", "Sonstige Verbindlichkeiten", "passiv", "P.C.8", "", "", ""),
    ("3720", "Verbindlichkeiten aus Lohn und Gehalt", "passiv", "P.C.8", "", "", ""),
    ("3730", "Verbindlichkeiten aus Lohn- und Kirchensteuer", "passiv", "P.C.8", "", "", ""),
    ("3740", "Verbindlichkeiten im Rahmen der sozialen Sicherheit", "passiv", "P.C.8", "", "", ""),
    ("3801", "Umsatzsteuer 7 %", "steuer", "P.C.8", "", "", ""),
    ("3802", "Umsatzsteuer aus innergemeinschaftlichem Erwerb 7 %", "steuer", "P.C.8", "", "", ""),
    ("3804", "Umsatzsteuer aus innergemeinschaftlichem Erwerb 19 %", "steuer", "P.C.8", "", "", ""),
    ("3806", "Umsatzsteuer 19 %", "steuer", "P.C.8", "", "", ""),
    ("3837", "Umsatzsteuer nach § 13b UStG 19 %", "steuer", "P.C.8", "", "", ""),
    ("3820", "Umsatzsteuer-Vorauszahlungen", "steuer", W, "", "", ""),
    ("3840", "Umsatzsteuer Vorjahr", "steuer", W, "", "", ""),
    ("3900", "Passive Rechnungsabgrenzung", "passiv", "P.D", "", "", ""),
    ("4120", "Steuerfreie Umsätze § 4 Nr. 1a UStG (Ausfuhr)", "ertrag", "", "1", "", "AUS"),
    ("4125", "Steuerfreie innergemeinschaftliche Lieferungen § 4 Nr. 1b UStG", "ertrag", "", "1", "", "IGL"),
    ("4300", "Erlöse 7 % USt", "ertrag", "", "1", "", "U7"),
    ("4400", "Erlöse 19 % USt", "ertrag", "", "1", "", "U19"),
    ("4731", "Gewährte Skonti 7 % USt", "ertrag", "", "1", "", "U7"),
    ("4736", "Gewährte Skonti 19 % USt", "ertrag", "", "1", "", "U19"),
    ("4743", "Gewährte Skonti steuerfreie innergemeinschaftliche Lieferungen", "ertrag", "", "1", "", "IGL"),
    ("4800", "Bestandsveränderungen fertige Erzeugnisse", "ertrag", "", "2", "", ""),
    ("4810", "Bestandsveränderungen unfertige Erzeugnisse", "ertrag", "", "2", "", ""),
    ("4830", "Sonstige betriebliche Erträge", "ertrag", "", "4", "MAHNGEBUEHR", ""),
    ("5000", "Aufwendungen für Roh-, Hilfs- und Betriebsstoffe", "aufwand", "", "5a", "", ""),
    ("5300", "Wareneingang 7 % Vorsteuer", "aufwand", "", "5a", "", "V7"),
    ("5400", "Wareneingang 19 % Vorsteuer", "aufwand", "", "5a", "", "V19"),
    ("5425", "Innergemeinschaftlicher Erwerb 19 % Vorsteuer und 19 % Umsatzsteuer", "aufwand", "", "5a", "",
     "IGE19"),
    ("5731", "Erhaltene Skonti 7 % Vorsteuer", "aufwand", "", "5a", "", "V7"),
    ("5736", "Erhaltene Skonti 19 % Vorsteuer", "aufwand", "", "5a", "", "V19"),
    ("5900", "Fremdleistungen", "aufwand", "", "5b", "", ""),
    ("5920", "Bauleistungen § 13b UStG 19 % Vorsteuer und 19 % Umsatzsteuer", "aufwand", "", "5b", "", "RC19"),
    ("6010", "Löhne", "aufwand", "", "6a", "", ""),
    ("6020", "Gehälter", "aufwand", "", "6a", "", ""),
    ("6110", "Gesetzliche soziale Aufwendungen", "aufwand", "", "6b", "", ""),
    ("6120", "Beiträge zur Berufsgenossenschaft", "aufwand", "", "6b", "", ""),
    ("6220", "Abschreibungen auf Sachanlagen", "aufwand", "", "7", "", ""),
    ("6260", "Sofortabschreibung geringwertiger Wirtschaftsgüter", "aufwand", "", "7", "", ""),
    ("6300", "Sonstige betriebliche Aufwendungen", "aufwand", "", "8", "", ""),
    ("6310", "Miete", "aufwand", "", "8", "", ""),
    ("6325", "Gas, Strom, Wasser", "aufwand", "", "8", "", ""),
    ("6400", "Versicherungen", "aufwand", "", "8", "", ""),
    ("6420", "Beiträge", "aufwand", "", "8", "", ""),
    ("6460", "Reparaturen und Instandhaltung von technischen Anlagen und Maschinen", "aufwand", "", "8", "", ""),
    ("6500", "Fahrzeugkosten", "aufwand", "", "8", "", ""),
    ("6600", "Werbekosten", "aufwand", "", "8", "", ""),
    ("6740", "Ausgangsfrachten", "aufwand", "", "8", "", ""),
    ("6800", "Porto", "aufwand", "", "8", "", ""),
    ("6805", "Telefon", "aufwand", "", "8", "", ""),
    ("6815", "Bürobedarf", "aufwand", "", "8", "", ""),
    ("6825", "Rechts- und Beratungskosten", "aufwand", "", "8", "", ""),
    ("6827", "Abschluss- und Prüfungskosten", "aufwand", "", "8", "", ""),
    ("6830", "Buchführungskosten", "aufwand", "", "8", "", ""),
    ("6855", "Nebenkosten des Geldverkehrs", "aufwand", "", "8", "GELDVERKEHR", ""),
    ("6930", "Forderungsverluste", "aufwand", "", "8", "", ""),
    ("7100", "Sonstige Zinsen und ähnliche Erträge", "ertrag", "", "11", "ZINSERTRAG", ""),
    ("7300", "Zinsen und ähnliche Aufwendungen", "aufwand", "", "13", "", ""),
    ("7600", "Körperschaftsteuer", "aufwand", "", "14", "", ""),
    ("7610", "Gewerbesteuer", "aufwand", "", "14", "", ""),
    ("7685", "Kfz-Steuer", "aufwand", "", "16", "", ""),
    ("9000", "Saldenvorträge Sachkonten", "neutral", "", "", "SALDENVORTRAG", ""),
    ("9008", "Saldenvorträge Debitoren", "neutral", "", "", "", ""),
    ("9009", "Saldenvorträge Kreditoren", "neutral", "", "", "", ""),
]

KONTEN = {"SKR03": SKR03, "SKR04": SKR04}

# Steuerschlüssel: Code, Bezeichnung, Art, Satz, {Rahmen: (Steuerkonto, Gegenkonto)}, Kz Bem., Kz St., Kz VSt, BU
STEUERSCHLUESSEL = [
    ("U19", "Umsatzsteuer 19 %", "UST", "19", {"SKR03": ("1776", ""), "SKR04": ("3806", "")}, "81", "", "", "3"),
    ("U7", "Umsatzsteuer 7 %", "UST", "7", {"SKR03": ("1771", ""), "SKR04": ("3801", "")}, "86", "", "", "2"),
    ("IGL", "Steuerfreie innergemeinschaftliche Lieferung", "IGL", "0", {}, "41", "", "", ""),
    ("AUS", "Steuerfreie Ausfuhrlieferung (Drittland)", "FREI", "0", {}, "43", "", "", ""),
    ("V19", "Vorsteuer 19 %", "VST", "19", {"SKR03": ("1576", ""), "SKR04": ("1406", "")}, "", "", "66", "9"),
    ("V7", "Vorsteuer 7 %", "VST", "7", {"SKR03": ("1571", ""), "SKR04": ("1401", "")}, "", "", "66", "8"),
    ("IGE19", "Innergemeinschaftlicher Erwerb 19 %", "IGE", "19",
     {"SKR03": ("1574", "1774"), "SKR04": ("1404", "3804")}, "89", "89", "61", "19"),
    ("IGE7", "Innergemeinschaftlicher Erwerb 7 %", "IGE", "7",
     {"SKR03": ("1572", "1772"), "SKR04": ("1402", "3802")}, "93", "93", "61", "18"),
    ("RC19", "Reverse Charge § 13b UStG 19 % (Leistungsempfänger)", "RC", "19",
     {"SKR03": ("1577", "1787"), "SKR04": ("1407", "3837")}, "84", "85", "67", "94"),
]

# Kontierungsregeln: Vorgang, Artikelart, Steuerschlüssel, {Rahmen: Konto}
KONTIERUNGSREGELN = [
    ("ERLOES", "", "U19", {"SKR03": "8400", "SKR04": "4400"}),
    ("ERLOES", "", "U7", {"SKR03": "8300", "SKR04": "4300"}),
    ("ERLOES", "", "IGL", {"SKR03": "8125", "SKR04": "4125"}),
    ("ERLOES", "", "AUS", {"SKR03": "8120", "SKR04": "4120"}),
    ("AUFWAND", "", "", {"SKR03": "3000", "SKR04": "5000"}),
    ("BESTAND", "", "", {"SKR03": "3970", "SKR04": "1000"}),
    ("BESTAND", "HALBZEUG", "", {"SKR03": "7000", "SKR04": "1040"}),
    ("BESTAND", "FERTIGARTIKEL", "", {"SKR03": "7110", "SKR04": "1110"}),
    ("BESTAND", "HANDELSWARE", "", {"SKR03": "3980", "SKR04": "1140"}),
    ("MATERIAL", "", "", {"SKR03": "3000", "SKR04": "5000"}),
    ("BV", "", "", {"SKR03": "8980", "SKR04": "4800"}),
    ("BV", "HALBZEUG", "", {"SKR03": "8960", "SKR04": "4810"}),
    ("SKONTO_G", "", "", {"SKR03": "8736", "SKR04": "4736"}),
    ("SKONTO_G", "", "U7", {"SKR03": "8731", "SKR04": "4731"}),
    ("SKONTO_G", "", "IGL", {"SKR03": "8743", "SKR04": "4743"}),
    ("SKONTO_E", "", "", {"SKR03": "3736", "SKR04": "5736"}),
    ("SKONTO_E", "", "V7", {"SKR03": "3731", "SKR04": "5731"}),
]

MAHNSTUFEN = [
    (1, "Zahlungserinnerung", 7, 10, "0.00", False,
     "sicher haben Sie übersehen, dass die folgenden Rechnungen bereits fällig sind. Wir bitten Sie, den offenen "
     "Betrag bis zum genannten Datum zu überweisen. Sollten Sie inzwischen gezahlt haben, betrachten Sie dieses "
     "Schreiben bitte als gegenstandslos."),
    (2, "1. Mahnung", 14, 10, "5.00", True,
     "trotz unserer Zahlungserinnerung konnten wir für die folgenden Rechnungen noch keinen Zahlungseingang "
     "feststellen. Bitte überweisen Sie den Gesamtbetrag inklusive Mahngebühr und Verzugszinsen (§ 288 BGB)."),
    (3, "2. Mahnung", 14, 7, "10.00", True,
     "leider sind die folgenden Rechnungen weiterhin unbezahlt. Wir fordern Sie hiermit nachdrücklich auf, den "
     "Gesamtbetrag umgehend zu begleichen."),
    (4, "3. und letzte Mahnung", 14, 7, "15.00", True,
     "dies ist unsere letzte Mahnung. Geht der Gesamtbetrag nicht bis zum genannten Datum bei uns ein, werden wir "
     "ohne weitere Ankündigung gerichtliche Schritte (gerichtliches Mahnverfahren) einleiten."),
]

# Basiszinssatz § 247 BGB laut Deutscher Bundesbank (Stand: Bekanntgabe vom 30.06.2026)
BASISZINSSAETZE = [("2025-07-01", "1.27"), ("2026-01-01", "1.27"), ("2026-07-01", "1.52")]


def grunddaten_anlegen():
    """Idempotent: ergänzt fehlende Konten/Schlüssel/Regeln; vorhandene Konten erhalten fehlende Zuordnungen."""
    from datetime import date

    from apps.stammdaten.models import Konto

    from .models import Basiszinssatz, Kontierungsregel, Mahnstufe, Steuerschluessel

    for rahmen, konten in KONTEN.items():
        for nr, bez, art, bil, guv, auto, st in konten:
            k, angelegt = Konto.objects.get_or_create(kontenrahmen=rahmen, nummer=nr, defaults={
                "bezeichnung": bez, "kontoart": art, "bilanz_position": bil, "guv_position": guv,
                "automatik": auto, "steuer_automatik": st})
            if not angelegt and not (k.bilanz_position or k.guv_position or k.automatik or k.steuer_automatik):
                k.bilanz_position, k.guv_position, k.automatik, k.steuer_automatik = bil, guv, auto, st
                k.save(update_fields=["bilanz_position", "guv_position", "automatik", "steuer_automatik"])
        for code, bez, art, satz, konten_map, kz_b, kz_s, kz_v, bu in STEUERSCHLUESSEL:
            st_nr, gegen_nr = konten_map.get(rahmen, ("", ""))
            Steuerschluessel.objects.get_or_create(kontenrahmen=rahmen, code=code, defaults={
                "bezeichnung": bez, "art": art, "satz": Decimal(satz),
                "steuerkonto": Konto.objects.filter(kontenrahmen=rahmen, nummer=st_nr).first() if st_nr else None,
                "gegenkonto": Konto.objects.filter(kontenrahmen=rahmen, nummer=gegen_nr).first() if gegen_nr else None,
                "kz_bemessung": kz_b, "kz_steuer": kz_s, "kz_vorsteuer": kz_v, "datev_bu": bu})
        for vorgang, artikelart, schluessel, konto_map in KONTIERUNGSREGELN:
            Kontierungsregel.objects.get_or_create(
                kontenrahmen=rahmen, vorgang=vorgang, artikelart=artikelart, steuerschluessel=schluessel,
                defaults={"konto": Konto.objects.get(kontenrahmen=rahmen, nummer=konto_map[rahmen])})
    for stufe, bez, tage, frist, gebuehr, zinsen, text in MAHNSTUFEN:
        Mahnstufe.objects.get_or_create(stufe=stufe, defaults={
            "bezeichnung": bez, "tage": tage, "frist_tage": frist, "gebuehr": Decimal(gebuehr), "zinsen": zinsen,
            "text": text})
    for ab, satz in BASISZINSSAETZE:
        Basiszinssatz.objects.get_or_create(gueltig_ab=date.fromisoformat(ab), defaults={"satz": Decimal(satz)})
