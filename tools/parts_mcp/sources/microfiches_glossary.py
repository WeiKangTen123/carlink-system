"""Best-effort English for the Italian part names on the Microfiches BMW / MINI sites.

The BMW and MINI shops are run by an Italian dealer, so part names come in
Italian ("RIVESTIMENTO PARAURTI CENTRALE POST."), sometimes abbreviated,
sometimes with German colour words, and sometimes in English (rows that are
no longer sold). `translate()` turns them into plain English word by word;
it does not reorder words, so the result reads like a catalogue line:
"Trim panel bumper centre rear".

Rules:
- Function words (DI, DEL, A, PER...) are dropped and do not count.
- Tokens with digits (M6X20, 18X24MM, S507A) are kept verbatim and do not count.
- If fewer than half of the remaining words are known, return None rather
  than a half-translated guess.
- `side_of()` reads left/right only from the name (SX, DX, SIN., DES.,
  SINISTRA, DESTRA, LEFT, RIGHT, LH, RH, and Honda's "L." / "R.").
"""

from __future__ import annotations

import re
from typing import Literal

Side = Literal["left", "right"]

# Multi-word phrases first (keys without dots). Each counts all its words as known.
PHRASES: dict[tuple[str, ...], str] = {
    ("MOLLA", "A", "GAS"): "gas strut",
    ("MOLLA", "PNEUMATICA"): "gas strut",
    ("VITE", "TESTA", "CIL"): "fillister-head screw",
    ("VITE", "A", "TESTA", "CILINDRICA"): "fillister-head screw",
    ("TESTA", "CIL"): "fillister head",
    ("TESTA", "CILINDRICA"): "fillister head",
    ("TESTA", "CROCIATA"): "Phillips head",
    ("VITE", "ESAGONALE"): "hex bolt",
    ("DADO", "ESAGONALE"): "hex nut",
    ("GRUPPO", "OTTICO"): "lamp unit",
    ("GRUPPO", "OTT"): "lamp unit",
    ("FARO", "ANTINEBBIA"): "fog light",
    ("FARO", "FENDINEBBIA"): "fog light",
    ("OCCHIELLO", "DI", "TRAINO"): "towing eye",
    ("OCCHIONE", "DI", "TRAINO"): "towing eye",
    ("GANCIO", "DI", "TRAINO"): "tow hook",
    ("NASTRO", "A", "STRAPPO"): "tear-off tape",
    ("CON", "FONDO"): "primed",
    ("MANO", "FONDO"): "primer coat",
    ("M", "FONDO"): "primer coat",
    ("MANO", "BASE"): "base coat",
    ("PIANO", "DI", "CARICO"): "load floor",
    ("COFANO", "POSTERIORE"): "trunk lid",
    ("COFANO", "POST"): "trunk lid",
    ("COFANO", "POSTER"): "trunk lid",
    ("COFANO", "BAGAGLIAIO"): "trunk lid",
    ("COFANO", "PORTABAG"): "trunk lid",
    ("COFANO", "MOTORE"): "bonnet",
    ("COFANO", "ANTERIORE"): "bonnet",
    ("COFANO", "ANT"): "bonnet",
    ("PROTEZIONE", "PEDONI"): "pedestrian protection",
    ("SENSORE", "AD", "ULTRASUONI"): "ultrasonic sensor",
    ("SPECCHIO", "RETROVISORE"): "mirror",
    ("RETROVISORE", "ESTERNO"): "outside mirror",
    ("LUCE", "DI", "RETROMARCIA"): "reversing light",
    ("COMANDO", "A", "VITE"): "spindle drive",
    ("RIBADINO", "AD", "ESPANSIONE"): "expanding rivet",
    ("RIVETTO", "CIECO"): "blind rivet",
    ("PARAURTI", "ANTERIORE"): "front bumper",
    ("PARAURTI", "ANT"): "front bumper",
    ("PARAURTI", "POSTERIORE"): "rear bumper",
    ("PARAURTI", "POST"): "rear bumper",
    ("FANALE", "POSTERIORE"): "rear light",
    ("FANALE", "POST"): "rear light",
    ("LUCE", "POSTERIORE"): "rear light",
    ("FIANCATA", "ANTERIORE"): "side panel front",
    ("FIANCATA", "POSTERIORE"): "side panel rear",
    ("PASSA", "RUOTA"): "wheel arch",
    ("SOFT", "CLOSE", "AUTOMATICO"): "soft-close automatic",
}

# Function words: dropped (or a tiny English word), never counted.
FUNCTION_WORDS: dict[str, str] = {
    "DI": "", "DEL": "", "DELLA": "", "DELLO": "", "DEI": "", "DELLE": "", "DEGLI": "",
    "A": "", "AD": "", "AL": "", "ALLA": "", "ALLO": "", "AI": "", "AGLI": "", "ALLE": "",
    "DA": "", "DAL": "", "DALLA": "", "SU": "", "SUL": "", "SULLA": "", "PER": "", "P": "",
    "E": "", "ED": "", "IL": "", "LO": "", "LA": "", "I": "", "GLI": "", "LE": "",
    "UN": "", "UNA": "", "UNO": "", "CON": "with", "SENZA": "without",
    "IN": "in", "NEL": "in", "NELLA": "in", "NELLO": "in", "NEI": "in", "NELLE": "in",
}

# Italian words and catalogue abbreviations (keys without the trailing dot).
ITALIAN: dict[str, str] = {
    # directions and sides
    "POSTERIORE": "rear", "POST": "rear", "POSTER": "rear", "POSTERIORI": "rear",
    "ANTERIORE": "front", "ANT": "front", "ANTER": "front", "ANTERIORI": "front",
    "SINISTRA": "left", "SINISTRO": "left", "SINISTRE": "left", "SINISTRI": "left",
    "SIN": "left", "SN": "left", "SX": "left",
    "DESTRA": "right", "DESTRO": "right", "DESTRE": "right", "DESTRI": "right",
    "DES": "right", "DS": "right", "DX": "right",
    "SUPERIORE": "upper", "SUP": "upper", "INFERIORE": "lower", "INF": "lower",
    "LATERALE": "lateral", "LATER": "lateral", "LAT": "lateral",
    "CENTRALE": "centre", "CENTR": "centre", "INTERNO": "inner", "INTERNA": "inner",
    "INTERIORE": "inner", "ESTERNO": "outer", "ESTERNA": "outer", "ESTERIORE": "outer",
    "VERTICALE": "vertical", "ORIZZONTALE": "horizontal", "FISSO": "fixed", "FISSA": "fixed",
    "MOBILE": "movable",
    # body panels and trim
    "PARAURTI": "bumper", "PORTELLONE": "tailgate", "PORTELL": "tailgate",
    "RIVESTIMENTO": "trim panel", "RIVEST": "trim panel", "RIV": "trim panel",
    "COPERTURA": "cover", "COPERCHIO": "cover", "CALOTTA": "cap", "MASCHERINA": "trim cover",
    "MASCH": "trim cover", "GRIGLIA": "grille", "GRIGLIE": "grilles", "PARAFANGO": "wing",
    "FIANCATA": "side panel", "COFANO": "lid", "BAGAGLIAIO": "luggage compartment",
    "PORTABAG": "luggage compartment", "PORTABAGAGLI": "luggage compartment",
    "PORTA": "door", "PORTIERA": "door", "SPORTELLO": "flap", "SPORTELLINO": "flap",
    "TETTO": "roof", "MONTANTE": "pillar", "SOTTOPORTA": "door sill", "LONGHERONE": "side member",
    "TRAVERSA": "cross member", "TRAVERSO": "cross member", "LAMIERA": "sheet metal",
    "PANNELLO": "panel", "PAVIMENTO": "floor", "PASSARUOTA": "wheel arch", "MODANATURA": "moulding",
    "MODANAT": "moulding", "LISTELLO": "strip", "LISTA": "strip", "CORNICE": "frame",
    "BORDO": "edge", "SPOILER": "spoiler", "DEFLETTORE": "deflector", "PARASPRUZZI": "mud flap",
    "ANTINCASTRO": "underride guard", "ANTINC": "underride guard", "PROTEZIONE": "protection",
    "PROT": "protection", "SCHERMO": "shield", "SCHERMATURA": "shield", "PELLICOLA": "film",
    "ANTISFREG": "anti-scuff", "ANTISFREGAMENTO": "anti-scuff", "TELAIO": "frame",
    "INSONORIZZAZIONE": "sound insulation", "ISOLAMENTO": "insulation", "ISOL": "insulation",
    "TERMICO": "thermal", "TERMICA": "thermal", "EMBLEMA": "emblem", "SCRITTA": "lettering",
    "PLACCHETTA": "plate", "TARGA": "licence plate", "PORTATARGA": "licence plate holder",
    "GOCCIOLATOIO": "drip rail", "CANALE": "channel", "CONVOGLIATORE": "duct", "CONVOGL": "duct",
    "CONDOTTO": "duct", "BOCCHETTA": "vent", "PRESA": "intake", "ARIA": "air", "RACCOLTA": "catch",
    "SCARICO": "drain", "ACQUA": "water", "CAVITÀ": "cavity", "CAVITA": "cavity",
    "PARTE": "part", "PEZZO": "part", "ELEMENTO": "element", "INSERTO": "insert",
    "PROLUNGA": "extension", "ANGOLO": "corner", "SEDE": "mount", "ALLOGGIAMENTO": "housing",
    "ALLOGGIAMEN": "housing", "ALLOGG": "housing", "ATTACCO": "mount", "APPOGGIO": "rest",
    "CHIUSURA": "closure", "AMMANNITA": "primed", "AMMANNITO": "primed", "FONDO": "bottom",
    "VERNICIATO": "painted", "VERNICIATA": "painted", "GREZZO": "unpainted", "GREZZA": "unpainted",
    # glazing and mirrors
    "VETRO": "glass", "CRISTALLO": "glass", "PARABREZZA": "windscreen", "LUNOTTO": "rear window",
    "PARASOLE": "sun protection", "SPECCHIO": "mirror", "RETROVISORE": "mirror",
    "SPECCHIETTO": "mirror",
    # lights
    "FANALE": "lamp", "FANALINO": "lamp", "LUCE": "light", "LUCI": "lights", "FARO": "headlight",
    "PROIETTORE": "headlight", "CATARIFRANGENTE": "reflector", "CATADIOTTRO": "reflector",
    "LAMPADINA": "bulb", "PORTALAMPADA": "bulb holder", "OTTICO": "optical", "OTT": "optical",
    "INDICATORE": "indicator", "LAMPEGGIATORE": "turn indicator", "FRECCIA": "turn indicator",
    "ANTINEBBIA": "fog", "FENDINEBBIA": "fog", "RETROMARCIA": "reversing", "INGOMBRO": "side marker",
    # fasteners and small parts
    "VITE": "screw", "BULLONE": "bolt", "DADO": "nut", "RONDELLA": "washer", "RIVETTO": "rivet",
    "RIBADINO": "rivet", "PERNO": "pin", "PRIGIONIERO": "stud", "CLIP": "clip",
    "FERMAGLIO": "clip", "MOLLETTA": "clip", "GRAFFA": "clip", "MORSETTO": "clamp",
    "FASCETTA": "clamp", "TAPPO": "plug", "TAPPI": "plugs", "BOCCOLA": "bush", "ANELLO": "ring",
    "ESAGONALE": "hex", "ESAG": "hex", "CIL": "cylinder", "CILINDRICA": "cylindrical",
    "CROCIATA": "cross", "COMBINATO": "combination", "FILETTATA": "threaded", "FILETTATO": "threaded",
    "PIASTRA": "plate", "TESTA": "head", "SCARTAMENTO": "spacer", "PIANA": "flat", "PIANO": "flat",
    "COLLARE": "collar", "CIECO": "blind",
    "ESPANSIONE": "expanding", "MORTISA": "mortise", "STAFFA": "bracket", "SUPPORTO": "bracket",
    "SUPP": "bracket", "SOSTEGNO": "support", "RINFORZO": "reinforcement", "TAMPONE": "buffer",
    "PARACOLPO": "buffer", "GUIDA": "guide", "GUARNIZIONE": "seal", "GUARNIZ": "seal",
    "GUARN": "seal", "TENUTA": "seal", "CERNIERA": "hinge", "SERRATURA": "lock",
    "BLOCCAGGIO": "locking", "ARRESTO": "stop", "RITEGNO": "retainer", "FISSAGGIO": "fastening",
    "NASTRO": "tape", "ADESIVO": "adhesive", "ADESIVA": "adhesive", "BIADESIVO": "double-sided tape",
    "PASSACAVO": "grommet", "GOMMINO": "grommet", "PASSANTE": "grommet",
    # mechanisms
    "MOLLA": "spring", "ELASTICO": "spring", "LEVA": "lever", "ARTIC": "link", "ARTICOLATA": "link",
    "ACCOPPIAMENTO": "coupling", "COMANDO": "drive", "AZIONAMENTO": "actuator",
    "ATTUATORE": "actuator", "MOTORINO": "motor", "MOTORE": "engine", "MANIGLIA": "handle",
    "PULSANTE": "button", "BOTTONE": "button", "INTERRUTTORE": "switch", "SENSORE": "sensor",
    "SENSOR": "sensor", "ULTRASUONI": "ultrasonic", "TELECAMERA": "camera", "ANTENNA": "antenna",
    "CAVO": "cable", "CABLAGGIO": "wiring harness", "CONNETTORE": "connector", "SPINA": "plug",
    "PIOGGIA": "rain", "TUBO": "hose", "TUBAZIONE": "pipe", "FLESSIBILE": "flexible",
    "SPRUZZATORE": "spray nozzle", "UGELLO": "nozzle", "LAVAFARI": "headlight washer",
    "TERGICRISTALLO": "wiper", "SPAZZOLA": "wiper blade", "BRACCIO": "arm", "AMMORTIZZATORE": "damper",
    "DISACCOPPIAMENTO": "decoupling", "CONVERTITORE": "converter", "FRIZIONE": "friction",
    "AUTOMATICO": "automatic", "AUTOMATICA": "automatic", "ELETTRICO": "electric",
    "ELETTRICA": "electric", "OCCHIELLO": "eyelet", "OCCHIONE": "eye", "TRAINO": "towing",
    "GANCIO": "hook", "CARICO": "load", "PEDONI": "pedestrian", "STRAPPO": "tear-off",
    "RADIATORE": "radiator", "CONDENSATORE": "condenser", "VENTOLA": "fan", "SERBATOIO": "tank",
    "POMPA": "pump", "FILTRO": "filter", "CENTRALINA": "control unit", "VASCHETTA": "reservoir",
    "CERCHIO": "rim", "RUOTA": "wheel", "PNEUMATICO": "tyre", "COPRIMOZZO": "hub cap",
    "SEDILE": "seat", "CINTURA": "belt", "VOLANTE": "steering wheel", "TAPPETO": "mat",
    # materials, finishes, colours
    "PLASTICA": "plastic", "MATERIA": "material", "SINTETICA": "synthetic", "GOMMA": "rubber",
    "METALLO": "metal", "ACCIAIO": "steel", "ALLUMINIO": "aluminium", "CROMATO": "chrome",
    "CROMATA": "chrome", "NERO": "black", "NERA": "black", "BIANCO": "white", "GRIGIO": "grey",
    "ARGENTO": "silver", "ROSSO": "red", "BLU": "blue", "VERDE": "green", "OPACO": "matt",
    "LUCIDO": "gloss", "UNIVERSALE": "universal", "BASE": "base", "MANO": "coat",
    "APPL": "applied", "INCLUSO": "insert",
}

# German colour / finish words used in BMW variant lines.
GERMAN: dict[str, str] = {
    "SCHWARZ": "black", "SCHW": "black", "GLANZSCHWARZ": "gloss black", "GLANZSCHW": "gloss black",
    "HOCHGLANZ": "high-gloss", "GLANZ": "gloss", "WEISS": "white", "SILBER": "silver",
    "GRAU": "grey", "ANTHRAZIT": "anthracite", "CHROM": "chrome", "ALU": "aluminium",
    "SATINIERT": "satinised", "SATIN": "satin", "MATT": "matt", "LACKIERT": "painted",
    "UNLACKIERT": "unpainted", "GRUNDIERT": "primed", "ROT": "red", "BLAU": "blue",
    "BRAUN": "brown", "DUNKEL": "dark", "HELL": "light",
}

# English catalogue words that pass through unchanged (rows no longer sold
# are listed in English, and some Italian lines mix in English words).
ENGLISH = frozenset(
    """
    LINK LEVER SPRING SUPPORT LEFT RIGHT UPPER LOWER FRONT REAR CENTRE CENTER INNER OUTER
    TRUNK LID TRIM PANEL TAIL TAILGATE VIBRATION ABSORBER LICENCE LICENSE PLATE HOLDER
    SMART OPENER SOFT CLOSE SNAP LOCK LETTERING TORX LONGLIFE KIT BASE SENSOR PDC CLIP
    COVER CAP BRACKET SCREW NUT BOLT WASHER RIVET SEAL GASKET HINGE LAMP LIGHT BULB
    BUMPER MOULDING MOLDING GRILLE MIRROR GLASS WINDOW DOOR HOOD BONNET FENDER WING
    SPOILER REFLECTOR BEAM SPACER FACE CORNER SIDE PLUG GROMMET RETAINER STAY GUIDE
    HOUSING SWITCH WIRE HARNESS CABLE HOSE PIPE TUBE VALVE PUMP FILTER ASSY ASSEMBLY SET
    COMP COMPLETE SUB UNIT MOTOR ACTUATOR HANDLE BUTTON EMBLEM BADGE STRIP SILL ROOF
    PILLAR FLOOR SHIELD GUARD DUCT VENT AIR WATER DRAIN CHANNEL BUFFER STOP MOUNT MOUNTING
    ARM BLADE WIPER NOZZLE WASHER RESERVOIR TANK FAN RADIATOR CONDENSER ENGINE CAMERA
    ANTENNA CONNECTOR SOCKET PRIMED PAINTED UNPAINTED CHROME BLACK WHITE GREY GRAY SILVER
    AUTOMATIC ELECTRIC MANUAL BODY TOW HOOK EYE TOWING PROTECTION PEDESTRIAN INSERT
    EXTENSION REINFORCEMENT PARTITION CARRIER PROTECTOR DEFLECTOR FLAP HEADLIGHT HEADLAMP
    FOG TAILLIGHT TAILLAMP INDICATOR LENS FILM FOAM PAD TAPE ADHESIVE OPENING
    """.split()
)

_TOKEN = re.compile(r"[A-Z0-9]*\d[A-Z0-9.*]*|[^\W\d_]+\.?", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.upper())


def _word(token: str) -> str:
    return token.rstrip(".")


def _is_code(token: str) -> bool:
    return any(c.isdigit() for c in token)


def _lookup(word: str) -> str | None:
    return ITALIAN.get(word) or GERMAN.get(word) or (word.lower() if word in ENGLISH else None)


def translate(name: str | None) -> str | None:
    """English for an Italian (or mixed) catalogue name, or None if under half is known."""
    if not name:
        return None
    toks = _tokens(name)
    out: list[str] = []
    known = total = 0
    i = 0
    while i < len(toks):
        words = [_word(t) for t in toks[i : i + 4]]
        for size in (4, 3, 2):
            key = tuple(words[:size])
            if len(key) == size and key in PHRASES:
                out.append(PHRASES[key])
                counted = sum(1 for w in key if w not in FUNCTION_WORDS and len(w) > 1)
                known += counted
                total += counted
                i += size
                break
        else:
            tok, word = toks[i], words[0]
            i += 1
            if _is_code(tok):
                out.append(tok)
            elif word in FUNCTION_WORDS:
                if FUNCTION_WORDS[word]:
                    out.append(FUNCTION_WORDS[word])
            elif len(word) == 1:
                out.append(word)  # "C" of C-clip, "M" of M Sport: kept, not counted
            else:
                total += 1
                english = _lookup(word)
                if english:
                    known += 1
                    out.append(english)
    if total == 0 or known * 2 < total:
        return None
    deduped: list[str] = []
    for piece in out:
        if not deduped or deduped[-1] != piece:
            deduped.append(piece)
    text = " ".join(" ".join(deduped).split())
    for word in ("with", "in"):  # a dangling function word reads badly
        text = re.sub(rf"\s+{word}$", "", text)
    return text[:1].upper() + text[1:] if text else None


_LEFT = {"SX", "SIN", "SN", "SINISTRA", "SINISTRO", "SINISTRE", "SINISTRI", "LEFT", "LH"}
_RIGHT = {"DX", "DES", "DS", "DESTRA", "DESTRO", "DESTRE", "DESTRI", "RIGHT", "RH"}
# Honda English: "SPACER, R. RR. BUMPER SIDE" (R. = right; RR. = rear is not a side)
_HONDA_L = re.compile(r"(?<![A-Z0-9.])L\.|\(L\)")
_HONDA_R = re.compile(r"(?<![A-Z0-9.])R\.|\(R\)")


def side_of(name: str | None) -> Side | None:
    """'left' / 'right' when the name states one side only, else None."""
    if not name:
        return None
    upper = name.upper()
    words = {_word(t) for t in _tokens(upper) if not _is_code(t)}
    left = bool(words & _LEFT) or bool(_HONDA_L.search(upper))
    right = bool(words & _RIGHT) or bool(_HONDA_R.search(upper))
    if left and not right:
        return "left"
    if right and not left:
        return "right"
    return None
