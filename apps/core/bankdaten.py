"""Prüfung von Bankdaten für den SEPA-Zahlungsverkehr: IBAN (ISO 13616, Modulo 97), BIC (ISO 9362) und
Gläubiger-Identifikationsnummer (EPC262-08, Modulo 97 ohne Geschäftsbereichskennung).

Die Funktionen `*_pruefen` liefern den normalisierten Wert oder werfen ValidationError; die `*_validator`
sind als Django-Feldvalidatoren verwendbar (leere Werte werden akzeptiert).
"""
import re

from django.core.exceptions import ValidationError

# IBAN-Längen der SEPA-Teilnehmerländer (EPC409-09, Stand 2025)
IBAN_LAENGEN = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20,
    "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GI": 23, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26,
    "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "ME": 22, "MT": 31, "NL": 18, "NO": 15,
    "PL": 28, "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "VA": 22, "AL": 28, "MD": 24,
    "MK": 19, "RS": 22,
}
BIC_MUSTER = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
GLAEUBIGER_MUSTER = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}$")


def normalisieren(wert):
    return re.sub(r"\s+", "", wert or "").upper()


def _mod97(zeichen):
    """Zeichenkette (Buchstaben A=10 … Z=35) als Zahl modulo 97."""
    zahl = "".join(str(int(c, 36)) for c in zeichen)
    return int(zahl) % 97


def iban_pruefen(wert):
    iban = normalisieren(wert)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        raise ValidationError(f"„{wert}“ ist keine gültige IBAN (Format: Ländercode, Prüfziffer, Kontokennung).")
    land = iban[:2]
    if land not in IBAN_LAENGEN:
        raise ValidationError(f"IBAN-Land {land} nimmt nicht am SEPA-Verfahren teil.")
    if len(iban) != IBAN_LAENGEN[land]:
        raise ValidationError(f"Eine IBAN aus {land} hat {IBAN_LAENGEN[land]} Stellen, nicht {len(iban)}.")
    if _mod97(iban[4:] + iban[:4]) != 1:
        raise ValidationError(f"IBAN {iban}: Prüfziffer falsch (Tippfehler?).")
    return iban


def bic_pruefen(wert):
    bic = normalisieren(wert)
    if not BIC_MUSTER.match(bic):
        raise ValidationError(f"„{wert}“ ist kein gültiger BIC (8 oder 11 Zeichen, z. B. COBADEFFXXX).")
    return bic


def glaeubiger_id_pruefen(wert):
    """Gläubiger-ID, z. B. DE98ZZZ09999999999 (Testnummer der Bundesbank)."""
    ci = normalisieren(wert)
    if not GLAEUBIGER_MUSTER.match(ci) or len(ci) > 35:
        raise ValidationError(f"„{wert}“ ist keine gültige Gläubiger-ID (Format z. B. DE98ZZZ09999999999).")
    if ci[:2] == "DE" and len(ci) != 18:
        raise ValidationError("Eine deutsche Gläubiger-ID hat 18 Stellen.")
    # Prüfziffer: nationale Kennung + Ländercode + „00“, Geschäftsbereichskennung (Stellen 5–7) bleibt außen vor
    if 98 - _mod97(ci[7:] + ci[:2] + "00") != int(ci[2:4]):
        raise ValidationError(f"Gläubiger-ID {ci}: Prüfziffer falsch.")
    return ci


def iban_validator(wert):
    if wert:
        iban_pruefen(wert)


def bic_validator(wert):
    if wert:
        bic_pruefen(wert)


def glaeubiger_id_validator(wert):
    if wert:
        glaeubiger_id_pruefen(wert)


def iban_maskiert(iban):
    """Für Belege/Vorabankündigung: DE89 **** **** **** 3000."""
    iban = normalisieren(iban)
    if len(iban) < 8:
        return iban
    return f"{iban[:4]} **** **** **** {iban[-4:]}"


def iban_formatiert(iban):
    iban = normalisieren(iban)
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))
