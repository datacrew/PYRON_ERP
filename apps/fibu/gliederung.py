"""Vereinfachte Gliederung von Bilanz (§ 266 HGB) und GuV (§ 275 Abs. 2 HGB, Gesamtkostenverfahren).

Jedes Sachkonto trägt einen Positionscode (Konto.bilanz_position bzw. Konto.guv_position).
Wechselkonten (z. B. Umsatzsteuer-Verrechnung) tragen „Aktivposition|Passivposition“: ein Sollsaldo
wird aktiviert, ein Habensaldo passiviert.
"""

AKTIVA = [
    ("A", "A. Anlagevermögen", [
        ("A.I", "I. Immaterielle Vermögensgegenstände"),
        ("A.II", "II. Sachanlagen"),
        ("A.III", "III. Finanzanlagen"),
    ]),
    ("B", "B. Umlaufvermögen", [
        ("B.I", "I. Vorräte"),
        ("B.II.1", "II.1. Forderungen aus Lieferungen und Leistungen"),
        ("B.II.4", "II.4. Sonstige Vermögensgegenstände"),
        ("B.IV", "IV. Kassenbestand, Guthaben bei Kreditinstituten"),
    ]),
    ("C", "C. Rechnungsabgrenzungsposten", [("C", "Aktive Rechnungsabgrenzung")]),
]

PASSIVA = [
    ("P.A", "A. Eigenkapital", [
        ("P.A.I", "I. Gezeichnetes Kapital"),
        ("P.A.II", "II. Kapitalrücklage"),
        ("P.A.IV", "IV. Gewinnvortrag/Verlustvortrag"),
        ("P.A.V", "V. Jahresüberschuss/Jahresfehlbetrag"),
    ]),
    ("P.B", "B. Rückstellungen", [("P.B", "Rückstellungen")]),
    ("P.C", "C. Verbindlichkeiten", [
        ("P.C.2", "2. Verbindlichkeiten gegenüber Kreditinstituten"),
        ("P.C.4", "4. Verbindlichkeiten aus Lieferungen und Leistungen"),
        ("P.C.8", "8. Sonstige Verbindlichkeiten"),
    ]),
    ("P.D", "D. Rechnungsabgrenzungsposten", [("P.D", "Passive Rechnungsabgrenzung")]),
]

# GuV: (Code, Bezeichnung, Vorzeichen) – Vorzeichen +1 = Ertrag (Habensaldo positiv), −1 = Aufwand
GUV = [
    ("1", "1. Umsatzerlöse", 1),
    ("2", "2. Erhöhung/Verminderung des Bestands an fertigen und unfertigen Erzeugnissen", 1),
    ("4", "4. Sonstige betriebliche Erträge", 1),
    ("5a", "5. a) Aufwendungen für Roh-, Hilfs- und Betriebsstoffe und für bezogene Waren", -1),
    ("5b", "5. b) Aufwendungen für bezogene Leistungen", -1),
    ("6a", "6. a) Löhne und Gehälter", -1),
    ("6b", "6. b) Soziale Abgaben und Aufwendungen für Altersversorgung", -1),
    ("7", "7. Abschreibungen auf Sachanlagen", -1),
    ("8", "8. Sonstige betriebliche Aufwendungen", -1),
    ("11", "11. Sonstige Zinsen und ähnliche Erträge", 1),
    ("13", "13. Zinsen und ähnliche Aufwendungen", -1),
    ("14", "14. Steuern vom Einkommen und vom Ertrag", -1),
    ("16", "16. Sonstige Steuern", -1),
]
GUV_ZWISCHENSUMMEN = {"8": "Betriebsergebnis", "13": "Finanzergebnis", "14": "Ergebnis nach Steuern"}

AKTIV_CODES = {c for _, _, pos in AKTIVA for c, _ in pos}
PASSIV_CODES = {c for _, _, pos in PASSIVA for c, _ in pos}
GUV_CODES = {c for c, _, _ in GUV}
POSITION_TEXT = {c: t for _, _, pos in AKTIVA + PASSIVA for c, t in pos} | {c: t for c, t, _ in GUV}
