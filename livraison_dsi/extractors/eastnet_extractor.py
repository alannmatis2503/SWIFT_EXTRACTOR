# hf_spaces/extractors/eastnet_extractor.py
"""
Extracteur EastNet RJE — Archive de messages SWIFT au format brut.
Format du fichier RJE: messages concaténés séparés par '$', chaque message ayant la structure:
  ${1:F01BEACCMCXA091...}{2:I202CITIUS33XXXXN}{3:{...}}{4:\n:20:REF\n:32A:DATEDEVMNT\n...-}{5:{...}}

Règle de direction (BLoc 2):
  {2:I...} → message SORTANT (envoyé par BEAC)
  {2:O...} → message ENTRANT  (reçu par BEAC)

Retourne:
  (incoming_rows, outgoing_rows,
   beaccmcx091_rows, exception_323201_rows, other_exceptions_rows,
   banque_de_france_rows, forex_rows, bdf_corr_exception_rows, missing_codes)
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Imports des utilitaires partagés ─────────────────────────────────────────

try:
    from extractors.mt202 import (
        parse_amount, parse_date_YYMMDD, detect_country_from_text, BIC_RE,
    )
    from extractors import bic_utils
    from extractors.bic_utils import (
        map_reglement_code, extract_4digit_code_from_f52d,
        extract_tresor_code_from_f50f, extract_ccf_4digit_code_from_f50f,
        load_forex_codes,
    )
    HAS_BIC_UTILS = True
except Exception as _e:
    logger.debug("eastnet_extractor: bic_utils non disponible: %s", _e)
    bic_utils = None  # type: ignore
    map_reglement_code = None  # type: ignore
    extract_4digit_code_from_f52d = None  # type: ignore
    extract_tresor_code_from_f50f = None  # type: ignore
    extract_ccf_4digit_code_from_f50f = None  # type: ignore
    load_forex_codes = None  # type: ignore
    HAS_BIC_UTILS = False

# Import des fonctions de post-traitement depuis mt_multi (réutilisation complète)
try:
    from extractors.mt_multi import (
        _postprocess_row_for_202_103,
        _extract_f52a_for_mt910,
        _extract_donneur_mt910_incoming,
        _extract_f58a_beneficiary,
        _check_f58a_323201,
        _check_eur_exception,
        _check_nivellement_exception,
        _check_salle_des_marches_exception,
        _check_f21_bc_mt202_outgoing,
        _should_reject_mt103,
        _citius33_f58a_fallback,
        _bdf_f58a_fallback,
        _check_mt202_outgoing_corr_exception,
        _fill_country_from_code,
        _fill_country_from_code_force,
        _extract_f72_comment,
        _extract_f70_comment,
        _extract_f72_comment_mt910,
        _extract_f21_reference_origine,
        _extract_receiver_bic as _mt_multi_extract_receiver_bic,
        _FALSE_BIC_WORDS,
        BDF_BICS,
    )
    HAS_MT_MULTI_HELPERS = True
except Exception as _e:
    logger.warning("eastnet_extractor: mt_multi helpers non disponibles: %s", _e)
    HAS_MT_MULTI_HELPERS = False

try:
    from extractors.mt103 import (
        extract_donneur_from_f50,
        extract_donneur_outgoing_mt103,
        extract_name_from_f50f_details,
    )
    HAS_MT103_DONNEUR = True
except Exception:
    extract_donneur_from_f50 = None  # type: ignore
    extract_donneur_outgoing_mt103 = None  # type: ignore
    extract_name_from_f50f_details = None  # type: ignore
    HAS_MT103_DONNEUR = False

# ── Patterns compilés ─────────────────────────────────────────────────────────

# Découpage par '$' entre blocs SWIFT
_MSG_SPLIT_RE = re.compile(r'\$(?=\{1:)')

# Extraction bloc 2 (direction + type MT)
_BLOCK2_RE = re.compile(r'\{2:([IO])(\d{3})', re.I)

# Extraction bloc 1 (BIC émetteur pour receiver_bic en entrant, sender_bic en sortant)
_BLOCK1_BIC_RE = re.compile(r'\{1:F\d{2}([A-Z0-9]{8,11})')

# Extraction LT 12 chars complet du bloc 1 (BIC8 + LT_id + branch3) ; permet de
# reconstituer un BIC11 propre (BIC8 + branch3) en supprimant le LT_id.
_BLOCK1_LT12_RE = re.compile(r'\{1:F\d{2}([A-Z0-9]{12})')

# Liste hardcodée des BIC11 des Directions Nationales BEAC (source : bic_codes.xlsx,
# feuille Sheet1). Un MT103 entrant CITI USD dont le bloc 1 pointe vers l'un de ces
# BIC est considéré comme une opération DN et route vers autres_exceptions.
# La centrale BEACCMCX091 (Services Centraux DOF) est volontairement exclue.
_BEAC_DN_BIC11 = frozenset({
    'BEACCMCX100',  # BEAC Direction Nationale Cameroun
    'BEACCMCX090',  # BEAC Services Centraux Yaoundé (≠ DOF)
    'BEACGQGQXXX',  # BEAC Direction Nationale Guinée Équatoriale
    'BEACGALIXXX',  # BEAC Direction Nationale Gabon
    'BEACCFCFXXX',  # BEAC Direction Nationale RCA
    'BEACCGCGXXX',  # BEAC Direction Nationale Congo
    'BEACTDNDXXX',  # BEAC Direction Nationale Tchad
})

# Extraction du BIC destinataire (block 2 pour O, champ header pour I)
_BLOCK2_DEST_BIC_RE = re.compile(r'\{2:[IO]\d{3}([A-Z]{4}[A-Z]{2}[A-Z0-9]{2,5})', re.I)

# Extraction block 4 (tout entre {4:\n et \n-} ou -})
_BLOCK4_RE = re.compile(r'\{4:\r?\n(.*?)\r?\n-\}', re.DOTALL)
_BLOCK4_FALLBACK_RE = re.compile(r'\{4:(.*?)-\}', re.DOTALL)

# Tags SWIFT bruts — ex: :20:, :32A:, :52A:, etc.
_SWIFT_TAG_RE = re.compile(r':([0-9]{2}[A-Z]?):', re.MULTILINE)

# Parse :32A: — format YYMMDDCCCMONTANT (ex: 250114USD124919,85)
_F32A_RAW_RE = re.compile(r'(\d{6})([A-Z]{3})([\d,]+(?:,\d{1,2})?)')

# Parse :61: en MT950 brut
_F61_CORE_RE = re.compile(
    r'^(?P<value_date>\d{6})(?P<entry_date>\d{4})?(?P<rev>[R])?(?P<cd>[CD])'
    r'(?P<funds>[A-Z])?(?P<amount>\d+[\d,]*)(?P<tail>.*)$'
)

# Identification de type de message dans détails MT950 (:86:, B/O, etc.)
_MT_ID_RE = re.compile(r'\b(202|103|910|900|950)\b')

# BIC pattern standard
_BIC_LOOSE_RE = re.compile(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b')

# Mots courants faux BIC (copie locale si mt_multi non dispo)
_LOCAL_FALSE_BIC_WORDS = frozenset([
    'CAMEROON', 'CAMEROUN', 'GABON', 'CONGO', 'TCHAD', 'CENTRAFRICAINE',
    'GUINEE', 'EQUATORIALE', 'DOUALA', 'YAOUNDE', 'MALABO', 'LIBREVILLE',
    'BRAZZAVILLE', 'BANGUI', 'NDJAMENA', 'INSTITUTION', 'IDENTIFIANT',
    'IDENTIFIER', 'BENEFICIAIRE', 'DONNEUR', 'ORDRE', 'PARTIE',
    'TRANSFER', 'PAYMENT', 'CREDIT', 'DEBIT', 'COMPTE', 'ACCOUNT',
])


def _get_false_bic_words():
    """Retourner l'ensemble des faux BIC (depuis mt_multi si disponible, sinon local)."""
    if HAS_MT_MULTI_HELPERS:
        try:
            return _FALSE_BIC_WORDS
        except Exception:
            pass
    return _LOCAL_FALSE_BIC_WORDS


# ── Parsing bas niveau des fichiers RJE ────────────────────────────────────────

def _split_rje_messages(
    text: str,
    allowed_mt_types: Optional[set] = None,
    allowed_directions: Optional[set] = None,
) -> List[str]:
    """Découper le texte RJE en blocs de messages individuels.

    Optimisation: si ``allowed_mt_types`` et/ou ``allowed_directions`` sont
    fournis, le bloc 2 de chaque message est inspecté en amont (regex rapide)
    pour ignorer les messages qui ne correspondent pas. Évite le parsing
    coûteux du bloc 4 pour les messages indésirables.

    Args:
        text: Contenu brut du fichier RJE.
        allowed_mt_types: Ensemble de codes MT à conserver (ex: {'202','103','910'}).
            None → tous les types.
        allowed_directions: Ensemble de directions à conserver
            ({'incoming'} et/ou {'outgoing'}). None → toutes.
    """
    # Normaliser les retours chariot
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Découper sur '$' suivi de '{1:'
    parts = _MSG_SPLIT_RE.split(text)

    do_filter = bool(allowed_mt_types) or bool(allowed_directions)

    messages: List[str] = []
    for p in parts:
        p = p.strip()
        if not p or '{1:' not in p:
            continue
        if do_filter:
            m = _BLOCK2_RE.search(p)
            if not m:
                # Sans bloc 2 valide: rejet immédiat (cohérent avec le pipeline)
                continue
            io_char = m.group(1).upper()
            mt_type = m.group(2)
            if allowed_mt_types and mt_type not in allowed_mt_types:
                continue
            if allowed_directions:
                direction = 'outgoing' if io_char == 'I' else 'incoming'
                if direction not in allowed_directions:
                    continue
        messages.append(p)
    return messages


def _detect_direction_and_mt(msg_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Détecter la direction et le type MT depuis le bloc 2.
    Retourne (direction, mt_type) ou (None, None).
    direction: 'incoming' ou 'outgoing'
    mt_type: '202', '103', '910', '950', '900', etc.
    """
    m = _BLOCK2_RE.search(msg_text)
    if not m:
        return None, None
    io_char = m.group(1).upper()
    mt_type = m.group(2)
    direction = 'outgoing' if io_char == 'I' else 'incoming'
    return direction, mt_type


def _extract_block4_text(msg_text: str) -> Optional[str]:
    """Extraire le contenu brut du bloc 4 (tags SWIFT)."""
    m = _BLOCK4_RE.search(msg_text)
    if m:
        return m.group(1).strip()
    # Fallback : entre {4: et -}
    m2 = _BLOCK4_FALLBACK_RE.search(msg_text)
    if m2:
        return m2.group(1).strip()
    return None


def _parse_swift_tags(block4_text: str) -> Dict[str, str]:
    """
    Parser les tags SWIFT bruts du bloc 4.
    Retourne un dict {TAG: valeur_multilignes}.
    Ex: {'20': 'C0050146834301', '32A': '250114USD124919,85', '52A': '/3582027880001\nUNAFCMCX'}
    """
    if not block4_text:
        return {}
    
    tags: Dict[str, str] = {}
    # Trouver toutes les positions des tags
    tag_positions = []
    for m in _SWIFT_TAG_RE.finditer(block4_text):
        tag_positions.append((m.group(1), m.start(), m.end()))
    
    for i, (tag, start, end) in enumerate(tag_positions):
        # Le contenu va de la fin du tag jusqu'au début du tag suivant (ou fin)
        if i + 1 < len(tag_positions):
            next_start = tag_positions[i + 1][1]
            content = block4_text[end:next_start].strip()
        else:
            content = block4_text[end:].strip()
        tags[tag] = content
    
    return tags


def _parse_swift_tag_occurrences(block4_text: str) -> List[Tuple[str, str]]:
    """
    Parser toutes les occurrences des tags SWIFT du bloc 4.
    Contrairement à _parse_swift_tags, conserve les tags répétés (ex: :61: en MT950).
    """
    if not block4_text:
        return []

    positions: List[Tuple[str, int, int]] = []
    for m in _SWIFT_TAG_RE.finditer(block4_text):
        positions.append((m.group(1), m.start(), m.end()))

    out: List[Tuple[str, str]] = []
    for i, (tag, _start, end) in enumerate(positions):
        if i + 1 < len(positions):
            next_start = positions[i + 1][1]
            content = block4_text[end:next_start].strip()
        else:
            content = block4_text[end:].strip()
        out.append((tag, content))
    return out


def _normalize_amount_like_mt950(raw: str) -> Optional[float]:
    """Normaliser un montant type MT950 (européen) en float."""
    if not raw:
        return None
    s = str(raw).replace('#', '').strip()
    s = s.replace('.', '').replace(',', '.')
    if s.endswith('.'):
        s = s[:-1]
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _parse_single_f61_entry(f61_text: str, f86_text: Optional[str], idx: int) -> Optional[Dict]:
    """Parser une occurrence :61: (RJE brut) vers le format attendu par match_f61_with_messages."""
    if not f61_text:
        return None

    line = f61_text.split('\n', 1)[0].strip()
    m = _F61_CORE_RE.match(line)
    if not m:
        return None

    value_date = m.group('value_date')
    cd = m.group('cd')
    amount_raw = m.group('amount')
    tail = (m.group('tail') or '').strip()

    ref_owner = None
    ref_serv = None

    # Séparer référence propriétaire et référence banque après //
    if '//' in tail:
        left, right = tail.split('//', 1)
        ref_owner = left.strip() or None
        ref_serv = right.strip().split()[0] if right.strip() else None
    else:
        ref_owner = tail.strip() or None

    # Identification code: chercher d'abord dans :86:, sinon dans :61:
    identification_code = None
    mt_src = f"{f86_text or ''} {line}"
    m_id = _MT_ID_RE.search(mt_src)
    if m_id:
        identification_code = m_id.group(1)

    return {
        'f61_index': idx,
        'cd': cd,
        'amount_raw': amount_raw,
        'amount': _normalize_amount_like_mt950(amount_raw),
        'ref_owner': ref_owner,
        'ref_serv': ref_serv,
        'identification_code': identification_code,
        'supplementary_details': (f86_text or '').strip() or None,
        'value_date': value_date,
    }


def extract_mt950_entries_from_rje_file(rje_path: Path) -> List[Dict]:
    """
    Extraire les écritures F61 depuis un fichier RJE contenant des MT950.
    Format de sortie identique à extractors.mt950.extract_mt950_entries.
    """
    rje_path = Path(rje_path)
    if not rje_path.exists():
        raise FileNotFoundError(f"Fichier RJE introuvable: {rje_path}")

    text = rje_path.read_text(encoding='utf-8', errors='replace')
    # Pré-filtrage rapide: ne garder que les MT950
    messages = _split_rje_messages(text, allowed_mt_types={'950'})
    out: List[Dict] = []

    for msg in messages:
        direction, mt_type = _detect_direction_and_mt(msg)
        if not direction or mt_type != '950':
            continue

        block4 = _extract_block4_text(msg)
        if not block4:
            continue

        occurrences = _parse_swift_tag_occurrences(block4)
        f61_counter = 0
        for i, (tag, value) in enumerate(occurrences):
            if tag != '61':
                continue
            f61_counter += 1
            f86_val = None
            if i + 1 < len(occurrences) and occurrences[i + 1][0] == '86':
                f86_val = occurrences[i + 1][1]
            parsed = _parse_single_f61_entry(value, f86_val, f61_counter)
            if parsed:
                out.append(parsed)

    return out


def extract_mt950_entries_from_rje_files(rje_paths: List[Path]) -> List[Dict]:
    """Extraire les écritures F61 depuis plusieurs fichiers RJE."""
    all_entries: List[Dict] = []
    for path in rje_paths:
        try:
            all_entries.extend(extract_mt950_entries_from_rje_file(path))
        except Exception as e:
            logger.warning("eastnet: extraction MT950 échouée sur %s: %s", path, e)
    return all_entries


def _get_block1_bic11(msg_text: str) -> Optional[str]:
    """Extraire le BIC11 propre (BIC8 + branch3) du bloc 1 en sautant le LT_id."""
    if not msg_text:
        return None
    m = _BLOCK1_LT12_RE.search(msg_text)
    if not m:
        return None
    lt = m.group(1).upper()
    return lt[:8] + lt[9:12]


def _get_sender_receiver_bics(msg_text: str, direction: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extraire les BICs émetteur et destinataire depuis les blocs 1 et 2.
    Retourne (sender_bic, receiver_bic) du point de vue BEAC.
    - entrant (O): block1 BIC = correspondant (sender vers BEAC), block2 dest = BEAC
    - sortant (I): block1 BIC = BEAC, block2 dest = correspondant (receiver)
    """
    # BIC dans block 1
    m1 = _BLOCK1_BIC_RE.search(msg_text)
    block1_bic = m1.group(1).upper() if m1 else None
    
    # BIC destinataire dans block 2
    m2 = _BLOCK2_DEST_BIC_RE.search(msg_text)
    block2_dest_bic = None
    if m2:
        raw = m2.group(1).upper()
        # Normaliser en 11 chars si 8 chars (ajouter XXX)
        if len(raw) == 8:
            raw = raw + 'XXX'
        block2_dest_bic = raw
    
    if direction == 'incoming':
        # sender = correspondant (block1), receiver = BEAC
        sender_bic = block1_bic
        receiver_bic = block2_dest_bic or 'BEACCMCXA091'
    else:
        # sender = BEAC (block1), receiver = correspondant (block2)
        sender_bic = block1_bic
        receiver_bic = block2_dest_bic
    
    return sender_bic, receiver_bic


# ── Extraction des champs depuis les tags SWIFT bruts ──────────────────────────

def _parse_32a(value: str) -> Dict:
    """Parser :32A:YYMMDDCCCMONTANT → {date_reference, devise, montant}."""
    result = {'date_reference': None, 'devise': None, 'montant': None}
    if not value:
        return result
    
    m = _F32A_RAW_RE.match(value.strip())
    if m:
        result['date_reference'] = parse_date_YYMMDD(m.group(1))
        result['devise'] = m.group(2).upper()
        result['montant'] = parse_amount(m.group(3))
    else:
        # Fallback: chercher 6 chiffres, 3 lettres, montant
        m_date = re.search(r'(\d{6})', value)
        if m_date:
            result['date_reference'] = parse_date_YYMMDD(m_date.group(1))
        m_cur = re.search(r'\b([A-Z]{3})\b', value)
        if m_cur:
            result['devise'] = m_cur.group(1)
        m_amt = re.search(r'([0-9]+[,.][\d,]+)', value)
        if m_amt:
            result['montant'] = parse_amount(m_amt.group(1))
    
    return result


def _extract_bic_from_tag_value(value: str, xlsx_path: Optional[str] = None) -> Optional[str]:
    """
    Extraire le BIC et son nom depuis la valeur d'un tag SWIFT.
    Retourne 'BIC/Nom' ou 'BIC' ou None.
    """
    if not value:
        return None
    
    false_bics = _get_false_bic_words()
    
    # Chercher un BIC standard dans la valeur
    for m in _BIC_LOOSE_RE.finditer(value):
        bic = m.group(1).upper()
        if bic not in false_bics and len(bic) >= 8:
            if HAS_BIC_UTILS:
                try:
                    name = bic_utils.map_code_to_name(bic, xlsx_path=xlsx_path)
                    if name:
                        return f"{bic}/{name}"
                except Exception:
                    pass
            return bic
    
    return None


def _extract_f52a_donneur(tags: Dict[str, str], full_block4: str, direction: str,
                           xlsx_path: Optional[str] = None) -> Dict:
    """
    Extraire le donneur d'ordre depuis les tags F52A/F52D.
    Retourne dict avec code_donneur_dordre, donneur_dordre, institution_name, pays_iso3.
    """
    result = {
        'code_donneur_dordre': None,
        'donneur_dordre': None,
        'institution_name': None,
        'pays_iso3': None,
    }
    
    f52a_val = tags.get('52A') or tags.get('52D') or tags.get('52B') or ''
    
    if not f52a_val:
        return result
    
    # Format typique: /ACCOUNT\nBIC ou juste BIC
    lines = [l.strip() for l in f52a_val.split('\n') if l.strip()]
    
    code_name = None
    
    # Chercher un BIC dans les lignes
    false_bics = _get_false_bic_words()
    for line in lines:
        # Enlever le préfixe /ACCOUNT si présent
        clean_line = re.sub(r'^/\S+\s*', '', line).strip()
        if not clean_line:
            clean_line = line.lstrip('/').strip()
        
        m_bic = _BIC_LOOSE_RE.search(clean_line)
        if m_bic:
            bic = m_bic.group(1).upper()
            if bic not in false_bics:
                if HAS_BIC_UTILS:
                    try:
                        name = bic_utils.map_code_to_name(bic, xlsx_path=xlsx_path)
                        if name:
                            code_name = f"{bic}/{name}"
                        else:
                            code_name = bic
                    except Exception:
                        code_name = bic
                else:
                    code_name = bic
                break
        
        # Chercher un code 4 chiffres (sous-participant)
        if HAS_BIC_UTILS and map_reglement_code and extract_4digit_code_from_f52d:
            code_4 = extract_4digit_code_from_f52d(f52a_val)
            if code_4:
                reg_info = map_reglement_code(code_4, xlsx_path=xlsx_path)
                if reg_info:
                    bic_reg = reg_info.get('bic', code_4)
                    code_name = f"{bic_reg}/{reg_info.get('name', '')}"
                    if reg_info.get('country'):
                        result['pays_iso3'] = reg_info['country']
                    break
    
    if code_name:
        if '/' in code_name:
            code_only, name_only = code_name.split('/', 1)
        else:
            code_only = code_name
            name_only = None
        result['code_donneur_dordre'] = code_only
        result['donneur_dordre'] = name_only if name_only else code_only
        result['institution_name'] = name_only if name_only else code_only
        
        # Remplir pays depuis le BIC
        if HAS_BIC_UTILS and not result['pays_iso3']:
            try:
                country = bic_utils.map_code_to_country(code_only, xlsx_path=xlsx_path)
                if country:
                    result['pays_iso3'] = country
            except Exception:
                pass
    
    return result


def _convert_rje_tags_to_verbose(tags: Dict[str, str], mt_type: str,
                                  direction: str, sender_bic: str, receiver_bic: str) -> str:
    """
    Convertir les tags SWIFT bruts en texte verbose attendu par les helpers mt_multi.
    Ce format mimique le texte extrait d'un PDF pour que les fonctions de post-processing
    (comme _check_f58a_323201, _check_nivellement_exception, etc.) puissent opérer.
    
    Les tags sont convertis comme suit:
      :52A: → F52A: contenu
      :58A: → F58A: contenu
      etc.
    """
    parts = []
    
    # Ajouter un en-tête fictif avec type MT et BICs
    parts.append(f"Identifier: fin.{mt_type}")
    if sender_bic:
        parts.append(f"Sender: {sender_bic}")
    if receiver_bic:
        parts.append(f"Receiver: {receiver_bic}")
    
    # Convertir chaque tag SWIFT en label verbose PDF
    tag_to_label = {
        '20': 'F20', '21': 'F21', '23B': 'F23B', '25': 'F25', '25P': 'F25P',
        '28C': 'F28C', '32A': 'F32A', '50F': 'F50F', '50A': 'F50A', '50K': 'F50K',
        '51A': 'F51A', '52A': 'F52A', '52D': 'F52D', '53A': 'F53A', '53B': 'F53B',
        '54A': 'F54A', '55A': 'F55A', '56A': 'F56A', '57A': 'F57A', '58A': 'F58A', '58D': 'F58D',
        '59': 'F59', '59A': 'F59A', '59F': 'F59F',
        '70': 'F70', '71A': 'F71A', '72': 'F72',
        '60F': 'F60F', '61': 'F61', '62F': 'F62F', '62M': 'F62M',
        '90D': 'F90D', '90C': 'F90C', '86': 'F86',
    }
    
    for tag, value in tags.items():
        label = tag_to_label.get(tag, f'F{tag}')
        parts.append(f"{label}: {value}")
    
    return '\n'.join(parts)


# ── Extraction d'un message MT202 depuis tags bruts ───────────────────────────

def _extract_mt202_from_tags(tags: Dict[str, str], verbose_text: str, direction: str,
                              source_label: str, sender_bic: str, receiver_bic: str,
                              xlsx_path: Optional[str] = None) -> Optional[Dict]:
    """Extraire un message MT202 depuis ses tags SWIFT bruts."""
    row: Dict = {
        'type_MT': 'fin.202',
        'reference': None,
        'reference_origine': None,
        'date_reference': None,
        'devise': None,
        'montant': None,
        'code_donneur_dordre': None,
        'donneur_dordre': None,
        'institution_name': None,
        'beneficiaire': None,
        'pays_iso3': None,
        'correspondant': None,
        'commentaires': None,
        'source_pdf': source_label,
        'sender_bic': sender_bic,
        'receiver_bic': receiver_bic,
        'code_banque': receiver_bic,
    }
    
    # Référence F20
    row['reference'] = (tags.get('20') or '').strip().upper() or None
    
    # Référence d'origine F21
    row['reference_origine'] = (tags.get('21') or '').strip() or None
    
    # Montant / date / devise depuis F32A
    f32_data = _parse_32a(tags.get('32A', ''))
    row.update(f32_data)
    
    # Donneur d'ordre (F52A)
    donneur_data = _extract_f52a_donneur(tags, verbose_text, direction, xlsx_path)
    row.update(donneur_data)
    
    if direction == 'incoming':
        row['beneficiaire'] = None  # Règle: bénéficiaire vide pour MT202 entrants

        # NOTE: Pour les RJE, on N'APPLIQUE PAS _postprocess_row_for_202_103.
        # Cette fonction est calibrée sur le texte verbose extrait des PDFs (SWIFTRef)
        # et a tendance à effacer code_donneur_dordre quand le BIC F52A n'est pas
        # exactement mappé dans bic_codes.xlsx, ce qui empêche ensuite le fallback
        # F58A de s'activer (cf. règle CITIUS33). _extract_f52a_donneur extrait déjà
        # BIC + codes 4 chiffres + nom mappé, en préservant le BIC brut quand il
        # n'est pas dans le référentiel — exactement ce qu'on veut pour le fallback.

        # Fallback F58A : si le BIC F52A n'est pas dans bic_codes.xlsx (ou absent),
        # on récupère le donneur effectif depuis F58A. Cette règle s'applique à
        # tous les MT202 entrants RJE (CITIUS33, BdF, autres correspondants).
        if HAS_MT_MULTI_HELPERS:
            row = _citius33_f58a_fallback(row, verbose_text, xlsx_path=xlsx_path)
            if sender_bic and sender_bic.upper()[:11] in BDF_BICS:
                row = _bdf_f58a_fallback(row, verbose_text, xlsx_path=xlsx_path)
        
    else:  # outgoing
        # Extraire le bénéficiaire depuis F58A
        if HAS_MT_MULTI_HELPERS:
            row = _postprocess_row_for_202_103(row, verbose_text, xlsx_path=xlsx_path)
            row = _extract_f58a_beneficiary(row, verbose_text, xlsx_path=xlsx_path)
            
            # MT202 sortants CITI/SCB: donneur = bénéficiaire
            if receiver_bic and receiver_bic.upper()[:8] in ('SCBLGB2L', 'CITIGB2L'):
                row['donneur_dordre'] = row.get('beneficiaire')
                row['institution_name'] = row.get('beneficiaire')
                f58a_val = tags.get('58A', '')
                m_bic = _BIC_LOOSE_RE.search(f58a_val)
                if m_bic and m_bic.group(1).upper() not in _get_false_bic_words():
                    row['code_donneur_dordre'] = m_bic.group(1).upper()
        else:
            # Extraction basique F58A
            f58a_val = tags.get('58A', '') or tags.get('58D', '')
            row['beneficiaire'] = _extract_bic_from_tag_value(f58a_val, xlsx_path)
    
    # Remplir pays
    if HAS_MT_MULTI_HELPERS:
        row = _fill_country_from_code(row, xlsx_path=xlsx_path)
    
    # Commentaire F72
    comment = None
    if HAS_MT_MULTI_HELPERS and direction == 'incoming':
        comment = _extract_f72_comment(verbose_text)
    row['commentaires'] = comment
    
    return row


# ── Extraction d'un message MT103 depuis tags bruts ───────────────────────────

def _extract_mt103_from_tags(tags: Dict[str, str], verbose_text: str, direction: str,
                              source_label: str, sender_bic: str, receiver_bic: str,
                              xlsx_path: Optional[str] = None) -> Optional[Dict]:
    """Extraire un message MT103 depuis ses tags SWIFT bruts."""
    row: Dict = {
        'type_MT': 'fin.103',
        'reference': None,
        'reference_origine': None,
        'date_reference': None,
        'devise': None,
        'montant': None,
        'code_donneur_dordre': None,
        'donneur_dordre': None,
        'institution_name': None,
        'beneficiaire': None,
        'pays_iso3': None,
        'correspondant': None,
        'commentaires': None,
        'source_pdf': source_label,
        'sender_bic': sender_bic,
        'receiver_bic': receiver_bic,
        'code_banque': receiver_bic,
        'f53a_raw': tags.get('53A') or tags.get('53B') or None,
        'f54a_raw': tags.get('54A') or None,
        'f55a_raw': tags.get('55A') or None,
        'f57a_raw': tags.get('57A') or None,
    }
    
    # Référence F20
    row['reference'] = (tags.get('20') or '').strip().upper() or None
    
    # Montant / date / devise F32A
    f32_data = _parse_32a(tags.get('32A', ''))
    row.update(f32_data)
    
    # Donneur d'ordre
    if direction == 'incoming':
        # Entrant: chercher dans F50A, F50F, F50K
        f50f_val = tags.get('50F') or tags.get('50A') or tags.get('50K') or ''
        if f50f_val and HAS_MT103_DONNEUR and extract_donneur_from_f50:
            try:
                donneur = extract_donneur_from_f50(f50f_val)
                if donneur:
                    row['donneur_dordre'] = donneur
                    row['institution_name'] = donneur
            except Exception:
                pass
        
        # Commentaire F70 pour MT103 entrant
        if HAS_MT_MULTI_HELPERS:
            comment = _extract_f70_comment(verbose_text)
            row['commentaires'] = comment
        
        # Bénéficiaire: extraire depuis F59
        f59_val = tags.get('59') or tags.get('59A') or tags.get('59F') or ''
        if f59_val:
            lines = [l.strip() for l in f59_val.split('\n') if l.strip()]
            # Ignorer la première ligne si c'est un numéro de compte (commence par /)
            for line in lines:
                if not line.startswith('/') and len(line) > 3:
                    row['beneficiaire'] = line
                    break
        
    else:  # outgoing
        # Sortant: chercher dans F50F (CUST/CM/BEAC/...)
        f50f_val = tags.get('50F') or tags.get('50A') or tags.get('50K') or ''

        # 1) Enrichissement code/nom/pays via bic_codes.xlsx :
        #    a) code Trésor (ex: 1001/2001/...) ou CCF long (10.323101.0.9114.0...) → mapping Reglement
        #    b) sinon code CCF 4-digit (ex: 1401) → mapping CCF
        if f50f_val and HAS_BIC_UTILS:
            f50f_info = None
            try:
                if extract_tresor_code_from_f50f:
                    f50f_info = extract_tresor_code_from_f50f(f50f_val, xlsx_path=xlsx_path)
            except Exception:
                f50f_info = None
            if not f50f_info:
                try:
                    if extract_ccf_4digit_code_from_f50f:
                        f50f_info = extract_ccf_4digit_code_from_f50f(f50f_val, xlsx_path=xlsx_path)
                except Exception:
                    f50f_info = None
            if f50f_info:
                bic_or_code = f50f_info.get('bic') or f50f_info.get('code')
                if bic_or_code and bic_or_code != 'NAN':
                    row['code_donneur_dordre'] = bic_or_code
                else:
                    # Pas de BIC dans le référentiel : conserver le code (Trésor/CCF)
                    row['code_donneur_dordre'] = f50f_info.get('code')
                if f50f_info.get('name'):
                    row['donneur_dordre'] = f50f_info['name']
                    row['institution_name'] = f50f_info['name']
                if f50f_info.get('country') and not row.get('pays_iso3'):
                    row['pays_iso3'] = f50f_info['country']

        # 2) Fallback nom uniquement si pas encore de donneur (parsing libre F50F)
        if not row.get('donneur_dordre') and f50f_val and HAS_MT103_DONNEUR and extract_donneur_outgoing_mt103:
            try:
                donneur = extract_donneur_outgoing_mt103(f50f_val)
                if donneur:
                    row['donneur_dordre'] = donneur
                    row['institution_name'] = donneur
            except Exception:
                pass
        if not row.get('donneur_dordre') and f50f_val and HAS_MT103_DONNEUR and extract_name_from_f50f_details:
            try:
                donneur = extract_name_from_f50f_details(f50f_val)
                if donneur:
                    row['donneur_dordre'] = donneur
                    row['institution_name'] = donneur
            except Exception:
                pass

        # Bénéficiaire F59
        f59_val = tags.get('59') or tags.get('59A') or tags.get('59F') or ''
        if f59_val:
            lines = [l.strip() for l in f59_val.split('\n') if l.strip()]
            for line in lines:
                if not line.startswith('/') and len(line) > 3:
                    row['beneficiaire'] = line
                    break
    
    # Détection du pays
    if HAS_BIC_UTILS and row.get('code_donneur_dordre'):
        try:
            country = bic_utils.map_code_to_country(row['code_donneur_dordre'], xlsx_path=xlsx_path)
            if country:
                row['pays_iso3'] = country
        except Exception:
            pass
    
    if not row.get('pays_iso3'):
        country_text = (tags.get('52A', '') + ' ' + tags.get('50F', '') + ' ' +
                        tags.get('57A', '') + ' ' + tags.get('58A', ''))
        row['pays_iso3'] = detect_country_from_text(country_text)
    
    return row


# ── Extraction d'un message MT910 depuis tags bruts ───────────────────────────

def _extract_mt910_from_tags(tags: Dict[str, str], verbose_text: str, direction: str,
                              source_label: str, sender_bic: str, receiver_bic: str,
                              xlsx_path: Optional[str] = None) -> Optional[Dict]:
    """Extraire un message MT910 depuis ses tags SWIFT bruts."""
    row: Dict = {
        'type_MT': 'fin.910',
        'reference': None,
        'reference_origine': None,
        'date_reference': None,
        'devise': None,
        'montant': None,
        'code_donneur_dordre': None,
        'donneur_dordre': None,
        'institution_name': None,
        'beneficiaire': None,
        'pays_iso3': None,
        'correspondant': None,
        'commentaires': None,
        'source_pdf': source_label,
        'sender_bic': sender_bic,
        'receiver_bic': receiver_bic,
        'code_banque': sender_bic or receiver_bic,
    }
    
    # Référence F20
    row['reference'] = (tags.get('20') or '').strip().upper() or None
    
    # Référence d'origine F21
    row['reference_origine'] = (tags.get('21') or '').strip() or None
    
    # Montant / date / devise F32A
    f32_data = _parse_32a(tags.get('32A', ''))
    row.update(f32_data)
    
    # Donneur d'ordre (F52A pour MT910)
    if HAS_MT_MULTI_HELPERS:
        if direction == 'incoming':
            row = _extract_donneur_mt910_incoming(row, verbose_text, xlsx_path=xlsx_path)
        else:
            row = _extract_f52a_for_mt910(row, verbose_text, xlsx_path=xlsx_path)
    else:
        donneur_data = _extract_f52a_donneur(tags, verbose_text, direction, xlsx_path)
        row.update(donneur_data)
        # Pour MT910: bénéficiaire = donneur
        row['beneficiaire'] = row.get('donneur_dordre')
    
    # Commentaire F72 pour MT910
    if HAS_MT_MULTI_HELPERS:
        comment = _extract_f72_comment_mt910(verbose_text)
        row['commentaires'] = comment
    
    # Fallback CITIUS33 sur F58A pour MT910 entrants
    if HAS_MT_MULTI_HELPERS and direction == 'incoming':
        if sender_bic and 'CITIUS33' in sender_bic.upper():
            row = _citius33_f58a_fallback(row, verbose_text, xlsx_path=xlsx_path)
    
    return row


# ── Extraction MT950 ──────────────────────────────────────────────────────────

def _extract_mt950_entries_from_tags(tags: Dict[str, str], source_label: str) -> List[Dict]:
    """
    Extraire les entrées F61 d'un message MT950 depuis ses tags.
    Retourne une liste de dicts F61.
    """
    entries = []
    
    # Compte depuis F25 ou F25P
    account = (tags.get('25P') or tags.get('25') or '').strip()
    
    # Toutes les entrées F61
    f61_raw = tags.get('61') or ''
    if not f61_raw:
        return entries
    
    # Plusieurs F61 peuvent être dans le bloc — chaque ligne est une entrée
    # Format: YYMMDD[MMDD]CD MONTANT Stype BANQUE RÉFÉRENCE // etc.
    for line in f61_raw.split('\n'):
        line = line.strip()
        if not line:
            continue
        entry = {'source_pdf': source_label, 'account': account}
        # Pattern F61: YYMMDD[MMDD](C|D|RD|RC)MONTANT S...
        m = re.match(
            r'(\d{6})(\d{4})?(C|D|RD|RC)(\d+[,.]?\d*)',
            line
        )
        if m:
            entry['date'] = parse_date_YYMMDD(m.group(1))
            entry['cd'] = 'C' if 'C' in m.group(3) else 'D'
            entry['montant'] = parse_amount(m.group(4))
            entry['raw'] = line
            entries.append(entry)
    
    return entries


# ── Extraction MT900 (confirmation de débit) depuis tags RJE ──────────────────

def _extract_mt900_from_tags(tags: Dict[str, str], source_label: str,
                              sender_bic: str, receiver_bic: str) -> Dict:
    """
    Extraire un MT900 (confirmation de débit) depuis les tags d'un message RJE.

    Champs SWIFT clés :
    - F20  : référence de la confirmation
    - F21  : référence d'origine (= F20 du MT103/MT202 sortant)  → clé de matching
    - F32A : date / devise / montant
    - F52A : donneur d'ordre (souvent BEACCMCX091)
    """
    row: Dict = {
        'type_MT': 'fin.900',
        'reference': None,
        'related_reference': None,
        'date_reference': None,
        'devise': None,
        'montant': None,
        'code_donneur_dordre': None,
        'donneur_dordre': None,
        'institution_name': None,
        'beneficiaire': None,
        'pays_iso3': None,
        'correspondant': None,
        'commentaires': None,
        'source_pdf': source_label,
        'sender_bic': sender_bic,
        'receiver_bic': receiver_bic,
        'code_banque': sender_bic or receiver_bic,
    }

    row['reference'] = (tags.get('20') or '').strip().upper() or None
    row['related_reference'] = (tags.get('21') or '').strip().upper() or None

    f32_data = _parse_32a(tags.get('32A', ''))
    row.update(f32_data)

    # Donneur d'ordre F52A (souvent BEACCMCX091)
    f52a = (tags.get('52A') or '').strip()
    if f52a:
        m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2,5})\b', f52a.upper())
        if m_bic:
            bic_code = m_bic.group(1)
            row['code_donneur_dordre'] = bic_code
            if HAS_BIC_UTILS and bic_utils is not None:
                try:
                    nm = bic_utils.get_name_for_code(bic_code)
                    if nm:
                        row['donneur_dordre'] = nm
                        row['institution_name'] = nm
                except Exception:
                    pass

    return row


def extract_mt900_from_rje_file(rje_path: Path,
                                 correspondant: str = "CITIUS33XXX",
                                 xlsx_path: Optional[str] = None) -> List[Dict]:
    """
    Extraire tous les MT900 (confirmations de débit) d'un fichier RJE.
    """
    rje_path = Path(rje_path)
    if not rje_path.exists():
        raise FileNotFoundError(f"Fichier RJE introuvable: {rje_path}")

    if HAS_BIC_UTILS:
        try:
            bic_utils.load_bic_mapping(xlsx_path)
        except Exception:
            pass

    text = rje_path.read_text(encoding='utf-8', errors='replace')
    # Pré-filtrage rapide: ne garder que les MT900
    messages = _split_rje_messages(text, allowed_mt_types={'900'})
    logger.info("eastnet MT900: %d messages détectés dans %s", len(messages), rje_path.name)

    rows: List[Dict] = []
    for idx, msg_text in enumerate(messages, start=1):
        direction, mt_type = _detect_direction_and_mt(msg_text)
        if mt_type != '900':
            continue
        sender_bic, receiver_bic = _get_sender_receiver_bics(msg_text, direction or 'incoming')
        block4_text = _extract_block4_text(msg_text)
        if not block4_text:
            continue
        tags = _parse_swift_tags(block4_text)
        source_label = f"voir message N°{idx} du fichier {rje_path.name}"
        try:
            row = _extract_mt900_from_tags(tags, source_label, sender_bic or '', receiver_bic or '')
            row['correspondant'] = correspondant
            row['direction'] = direction or 'incoming'
            rows.append(row)
        except Exception as e:
            logger.warning("eastnet MT900: erreur message %d: %s", idx, e)

    logger.info("eastnet MT900: %s → %d MT900 extraits", rje_path.name, len(rows))
    return rows


def extract_mt900_from_rje_files(rje_paths: List[Path],
                                  correspondant: str = "CITIUS33XXX",
                                  xlsx_path: Optional[str] = None) -> List[Dict]:
    """Extraire les MT900 depuis plusieurs fichiers RJE et fusionner."""
    all_rows: List[Dict] = []
    for p in rje_paths:
        try:
            all_rows.extend(extract_mt900_from_rje_file(p, correspondant=correspondant, xlsx_path=xlsx_path))
        except Exception as e:
            logger.error("eastnet MT900: erreur sur %s: %s", p, e)
    return all_rows


# ── Routeur principal ─────────────────────────────────────────────────────────

def _process_single_message(msg_text: str, source_file: str, msg_idx: int,
                             xlsx_path: Optional[str] = None,
                             forex_codes: set = None,
                             correspondant: str = '') -> Tuple[Optional[Dict], str, str]:
    """
    Traiter un message RJE individuel.
    Retourne (row, direction, category) où category ∈ {
        'main', 'beaccmcx091', 'exception_323201', 'other_exception',
        'banque_de_france', 'forex', 'bdf_corr_exception', 'reject'
    }
    """
    if forex_codes is None:
        forex_codes = set()
    
    # 1. Détecter direction et type MT
    direction, mt_type = _detect_direction_and_mt(msg_text)
    if not direction or not mt_type:
        logger.debug("eastnet: message %d ignoré (pas de bloc 2 valide)", msg_idx)
        return None, '', 'reject'
    
    # 2. Filtrer les types valides (202, 103, 910 — on ignore 950, 900, etc. ici)
    if mt_type not in ('202', '103', '910'):
        logger.debug("eastnet: message %d ignoré (type MT%s non supporté)", msg_idx, mt_type)
        return None, direction, 'reject'
    
    # 3. Extraire les BICs émetteur/destinataire
    sender_bic, receiver_bic = _get_sender_receiver_bics(msg_text, direction)
    
    # 4. Extraire le bloc 4
    block4_text = _extract_block4_text(msg_text)
    if not block4_text:
        logger.debug("eastnet: message %d ignoré (bloc 4 manquant)", msg_idx)
        return None, direction, 'reject'
    
    # 5. Parser les tags SWIFT bruts
    tags = _parse_swift_tags(block4_text)
    
    # 6. Convertir en texte verbose pour les helpers mt_multi
    verbose_text = _convert_rje_tags_to_verbose(
        tags, mt_type, direction, sender_bic or '', receiver_bic or ''
    )
    
    # Source label
    source_label = f"voir message N°{msg_idx} du fichier {source_file}"
    
    # 7. Extraire selon le type MT
    row: Optional[Dict] = None
    
    if mt_type == '202':
        row = _extract_mt202_from_tags(
            tags, verbose_text, direction, source_label,
            sender_bic or '', receiver_bic or '', xlsx_path=xlsx_path
        )
    elif mt_type == '103':
        row = _extract_mt103_from_tags(
            tags, verbose_text, direction, source_label,
            sender_bic or '', receiver_bic or '', xlsx_path=xlsx_path
        )
    elif mt_type == '910':
        row = _extract_mt910_from_tags(
            tags, verbose_text, direction, source_label,
            sender_bic or '', receiver_bic or '', xlsx_path=xlsx_path
        )
    
    if not row:
        return None, direction, 'reject'
    
    # 8. Normalisation commune
    row['direction'] = direction
    for key in ('date_reference', 'reference', 'type_MT', 'pays_iso3', 'beneficiaire',
                 'montant', 'devise', 'source_pdf', 'commentaires', 'correspondant',
                 'code_donneur_dordre', 'donneur_dordre', 'institution_name'):
        if key not in row:
            row[key] = None
        # Garantir qu'aucun champ texte n'est un tuple ou liste (ex: résultat BIC non attendu)
        val = row[key]
        if isinstance(val, (list, tuple)):
            row[key] = val[0] if val and val[0] is not None else (val[1] if len(val) > 1 and val[1] is not None else None)
    if not row.get('institution_name') and row.get('donneur_dordre'):
        row['institution_name'] = row['donneur_dordre']
    
    # 9. Appliquer les règles de classification / exception (comme mt_multi)
    if not HAS_MT_MULTI_HELPERS:
        return row, direction, 'main'
    
    # ── Règle BEACCMCX091 (MT202 sortants) ──
    # §11.1 — Annulation : si sender commence par BEACCMCX et receiver est
    # SCBLGB2LXXX ou CITIGB2LXXX, l'exception BEACCMCX091 est annulée et le
    # message redevient normal (rows / summary). Cf. mt_multi.py L1763-1768
    # pour la même logique côté flux PDF.
    if mt_type == '202' and direction == 'outgoing':
        f52a_val = tags.get('52A', '')
        if 'BEACCMCX091' in f52a_val.upper():
            _sender_up = (sender_bic or '').upper()
            _receiver_up = (receiver_bic or '').upper()
            _cancel_beaccmcx091 = (
                _sender_up.startswith('BEACCMCX')
                and _receiver_up in ('SCBLGB2LXXX', 'CITIGB2LXXX')
            )
            if not _cancel_beaccmcx091:
                return row, direction, 'beaccmcx091'
            logger.debug(
                "eastnet: MT202 sortant msg %d — exception BEACCMCX091 annulée "
                "(sender=%s, receiver=%s)", msg_idx, _sender_up, _receiver_up
            )
    
    # ── Règle BEACCMCX091 (MT910 entrants — F50A strict + fallback BIC) ──
    # RJE only. Cette règle suit deux temps :
    #   1) Exception : si F50A contient BEACCMCX091 (ou si code_donneur_dordre
    #      l'a déjà fourni via F52A/F52D historique), on classe en exception
    #      'beaccmcx091'.
    #   2) Sinon, si F50A contient un BIC ≠ BEACCMCX091, on l'utilise comme
    #      fallback pour enrichir code_donneur_dordre / donneur_dordre /
    #      pays_iso3 via bic_codes.xlsx — uniquement quand ces champs sont
    #      vides après l'extraction primaire (F52A/F52D).
    # On NE teste plus F25P : ce tag contient quasi-systématiquement
    # BEACCMCX091 sur le flux CITI USD, ce qui produirait des faux positifs
    # (p.ex. C0050135966101 dont le donneur réel est BGFICGCG en F50A).
    if mt_type == '910' and direction == 'incoming':
        f50a_raw = (tags.get('50A') or '').strip()
        f50a_upper = f50a_raw.upper()
        code_d_upper = (row.get('code_donneur_dordre') or '').upper()

        if 'BEACCMCX091' in f50a_upper or 'BEACCMCX091' in code_d_upper:
            return row, direction, 'beaccmcx091'

        # Fallback F50A → enrichissement donneur/pays via bic_codes.xlsx
        if f50a_raw and HAS_BIC_UTILS and bic_utils is not None:
            m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2,5})\b', f50a_upper)
            if m_bic:
                bic_f50a = m_bic.group(1)
                try:
                    name_f50a = bic_utils.get_name_for_code(bic_f50a, xlsx_path=xlsx_path)
                except Exception:
                    name_f50a = None
                try:
                    country_f50a = bic_utils.map_code_to_country(bic_f50a, xlsx_path=xlsx_path)
                except Exception:
                    country_f50a = None
                if name_f50a:
                    if not row.get('code_donneur_dordre'):
                        row['code_donneur_dordre'] = bic_f50a
                    if not row.get('donneur_dordre'):
                        row['donneur_dordre'] = name_f50a
                    if not row.get('beneficiaire'):
                        row['beneficiaire'] = name_f50a
                    if not row.get('pays_iso3') and country_f50a:
                        row['pays_iso3'] = country_f50a
    
    # ── Règle 323201 (MT202 entrants) ──
    if mt_type == '202' and direction == 'incoming':
        if _check_f58a_323201(verbose_text):
            return row, direction, 'exception_323201'
    
    # ── Règle EUR exception (T2PI/T2RM/T2PL) ──
    eur_exc = _check_eur_exception(row, direction)
    if eur_exc:
        row['commentaires'] = eur_exc
        return row, direction, 'other_exception'
    
    # ── Règle nivellement ──
    niv_exc = _check_nivellement_exception(row, verbose_text, direction)
    if niv_exc:
        comment = row.get('commentaires')
        row['commentaires'] = f"{comment} / {niv_exc}" if comment else niv_exc
        return row, direction, 'other_exception'
    
    # ── Règle §10.3 — BC dans F21 (MT202 sortant) → salle des marchés ──
    # Si la référence d'origine F21 contient "BC", le message est routé vers
    # other_exceptions avec le commentaire "opération salle des marchés".
    # §11.2 — Annulation : si receiver est SCBLGB2LXXX ou CITIGB2LXXX,
    # l'exception est annulée (message redevient normal, sans commentaire).
    if mt_type == '202' and direction == 'outgoing':
        if _check_f21_bc_mt202_outgoing(verbose_text):
            _receiver_up = (receiver_bic or '').upper()
            _cancel_bc = _receiver_up in ('SCBLGB2LXXX', 'CITIGB2LXXX')
            if not _cancel_bc:
                comment = row.get('commentaires')
                bc_label = 'opération salle des marchés'
                row['commentaires'] = f"{comment} / {bc_label}" if comment else bc_label
                return row, direction, 'other_exception'
            logger.debug(
                "eastnet: MT202 sortant msg %d — exception BC F21 annulée (receiver=%s)",
                msg_idx, _receiver_up,
            )

    # ── Règle salle des marchés (MT910 entrant + IBAN FR… dans F25P) ──
    # Règle minoritaire issue de mt_multi.py L747 (`_check_salle_des_marches_exception`).
    # Pas d'annulation prévue.
    sdm_exc = _check_salle_des_marches_exception(row, verbose_text, direction)
    if sdm_exc:
        comment = row.get('commentaires')
        row['commentaires'] = f"{comment} / {sdm_exc}" if comment else sdm_exc
        return row, direction, 'other_exception'
    
    # ── Règle MT103 USD rejet vers BANQUE DE FRANCE ──
    if mt_type == '103' and direction == 'incoming':
        if _should_reject_mt103(row):
            return row, direction, 'banque_de_france'
    
    # ── Règle forex (MT910 entrants) ──
    if mt_type == '910' and direction == 'incoming':
        code_d = (row.get('code_donneur_dordre') or '').upper()
        if code_d[:8] in forex_codes or code_d in forex_codes:
            return row, direction, 'forex'

    # ── Règle "exception BC" (MT910 entrants Standard) ──
    # Pour les MT910 entrants depuis Standard Chartered (SCBLGB2LXXX), si la
    # référence d'origine (F21) se termine par "BC", router en autres_exceptions.
    if (
        mt_type == '910'
        and direction == 'incoming'
        and (correspondant or '').upper().startswith('SCBLGB2L')
    ):
        ref_orig = (row.get('reference_origine') or '').strip().upper()
        if ref_orig.endswith('BC'):
            comment = row.get('commentaires')
            bc_label = 'exception BC'
            row['commentaires'] = f"{comment} / {bc_label}" if comment else bc_label
            return row, direction, 'other_exception'
    
    # ── Règle exception correspondant BdF (MT202 sortants) ──
    if mt_type == '202' and direction == 'outgoing':
        recv = (receiver_bic or '').upper()
        # Normaliser en 11 chars
        if len(recv) == 8:
            recv = recv + 'XXX'
        exc_comment = _check_mt202_outgoing_corr_exception(verbose_text, recv)
        if exc_comment:
            row['commentaires'] = exc_comment
            return row, direction, 'bdf_corr_exception'

    # ── Règle "opérations DN" (CITI USD entrants MT103 vers une LT BEAC DN) ──
    # Pour les MT103 entrants depuis CITI USD, le bloc 1 contient la LT BEAC qui
    # a reçu le message. Si elle figure dans la whitelist des Directions Nationales
    # (BIC11 normalisé), le message route vers autres_exceptions avec le commentaire
    # "opérations DN". La centrale BEACCMCX091 n'est pas dans la whitelist.
    if (
        mt_type == '103'
        and direction == 'incoming'
        and (correspondant or '').upper().startswith('CITIUS33')
    ):
        bic11 = _get_block1_bic11(msg_text)
        if bic11 and bic11 in _BEAC_DN_BIC11:
            comment = row.get('commentaires')
            dn_label = 'opérations DN'
            row['commentaires'] = f"{comment} / {dn_label}" if comment else dn_label
            return row, direction, 'other_exception'

    return row, direction, 'main'


# ── Point d'entrée public ─────────────────────────────────────────────────────

def extract_from_rje_file(
    rje_path: Path,
    correspondant: str = "CITIUS33XXX",
    xlsx_path: Optional[str] = None,
    allowed_directions: Optional[set] = None,
    allowed_mt_types: Optional[set] = None,
) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], Dict]:
    """
    Extraire tous les messages SWIFT d'un fichier RJE.

    Args:
        rje_path: Chemin du fichier .rje
        correspondant: Code BIC 11-caractères du correspondant
            (ex: 'CITIUS33XXX' pour CITI USD, 'CITIGB2LXXX' pour CITI EUR,
            'BDFEFRPPXXX' pour Banque de France, 'SCBLGB2LXXX' pour Standard).
        xlsx_path: Chemin vers bic_codes.xlsx (optionnel)
        allowed_directions: Ensemble de directions à conserver
            ({'incoming'} pour mode entrants, {'outgoing'} pour sortants).
            None = pas de pré-filtrage par direction (comportement historique).
        allowed_mt_types: Ensemble de codes MT à conserver. None applique
            le filtre par défaut {'202','103','910'}.

    Returns:
        (incoming_rows, outgoing_rows,
         beaccmcx091_rows, exception_323201_rows, other_exceptions_rows,
         banque_de_france_rows, forex_rows, bdf_corr_exception_rows, missing_codes)
    """
    if allowed_mt_types is None:
        allowed_mt_types = {'202', '103', '910'}
    rje_path = Path(rje_path)
    if not rje_path.exists():
        raise FileNotFoundError(f"Fichier RJE introuvable: {rje_path}")
    
    # Lecture du fichier
    try:
        text = rje_path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        logger.error("eastnet: impossible de lire %s: %s", rje_path, e)
        return [], [], [], [], [], [], [], [], {'unmapped': set(), 'empty': set()}
    
    # Précharger le mapping BIC
    if HAS_BIC_UTILS:
        try:
            bic_utils.load_bic_mapping(xlsx_path)
        except Exception as e:
            logger.debug("eastnet: préchargement BIC échoué: %s", e)
    
    # Charger les codes forex
    forex_codes: set = set()
    if HAS_BIC_UTILS and load_forex_codes:
        try:
            forex_codes = load_forex_codes(xlsx_path)
        except Exception as e:
            logger.debug("eastnet: chargement forex échoué: %s", e)
    
    # Découper les messages avec pré-filtrage rapide (sur le bloc 2)
    messages = _split_rje_messages(
        text,
        allowed_mt_types=allowed_mt_types,
        allowed_directions=allowed_directions,
    )
    logger.info("eastnet: %d messages détectés dans %s (filtres: types=%s, directions=%s)",
                len(messages), rje_path.name, allowed_mt_types, allowed_directions)
    
    # Catégories de résultats
    incoming_rows: List[Dict] = []
    outgoing_rows: List[Dict] = []
    beaccmcx091_rows: List[Dict] = []
    exception_323201_rows: List[Dict] = []
    other_exceptions_rows: List[Dict] = []
    banque_de_france_rows: List[Dict] = []
    forex_rows: List[Dict] = []
    bdf_corr_exception_rows: List[Dict] = []
    missing_codes: Dict = {'unmapped': set(), 'empty': set()}
    
    for idx, msg_text in enumerate(messages, start=1):
        try:
            row, direction, category = _process_single_message(
                msg_text, rje_path.name, idx,
                xlsx_path=xlsx_path, forex_codes=forex_codes,
                correspondant=correspondant,
            )
            
            if row is None or category == 'reject':
                continue
            
            # Affecter le correspondant
            row['correspondant'] = correspondant
            
            # Router vers la bonne catégorie
            if category == 'beaccmcx091':
                beaccmcx091_rows.append(row)
            elif category == 'exception_323201':
                exception_323201_rows.append(row)
            elif category == 'other_exception':
                other_exceptions_rows.append(row)
            elif category == 'banque_de_france':
                banque_de_france_rows.append(row)
            elif category == 'forex':
                forex_rows.append(row)
            elif category == 'bdf_corr_exception':
                bdf_corr_exception_rows.append(row)
            else:  # 'main'
                if direction == 'incoming':
                    incoming_rows.append(row)
                else:
                    outgoing_rows.append(row)
        
        except Exception as e:
            logger.warning("eastnet: erreur traitement message %d de %s: %s", idx, rje_path.name, e)
    
    logger.info(
        "eastnet: %s → %d entrants, %d sortants, %d BEAC, %d 323201, "
        "%d autres exc, %d BdF, %d forex, %d bdf_corr",
        rje_path.name, len(incoming_rows), len(outgoing_rows), len(beaccmcx091_rows),
        len(exception_323201_rows), len(other_exceptions_rows), len(banque_de_france_rows),
        len(forex_rows), len(bdf_corr_exception_rows)
    )
    
    return (incoming_rows, outgoing_rows, beaccmcx091_rows, exception_323201_rows,
            other_exceptions_rows, banque_de_france_rows, forex_rows,
            bdf_corr_exception_rows, missing_codes)


def extract_from_rje_files(
    rje_paths: List[Path],
    correspondant: str = "CITIUS33XXX",
    xlsx_path: Optional[str] = None,
    allowed_directions: Optional[set] = None,
    allowed_mt_types: Optional[set] = None,
) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], Dict]:
    """
    Extraire depuis plusieurs fichiers RJE et fusionner les résultats.

    ``allowed_directions`` et ``allowed_mt_types`` sont propagés au pré-filtre
    afin de n'analyser que les messages pertinents pour le sous-mode courant
    (ex: Mode 1 → entrants, Mode 2 → sortants).
    """
    all_incoming: List[Dict] = []
    all_outgoing: List[Dict] = []
    all_beaccmcx091: List[Dict] = []
    all_exc_323201: List[Dict] = []
    all_other_exc: List[Dict] = []
    all_bdf: List[Dict] = []
    all_forex: List[Dict] = []
    all_bdf_corr: List[Dict] = []
    all_missing: Dict = {'unmapped': set(), 'empty': set()}
    
    for path in rje_paths:
        try:
            (inc, out, beac, exc323, other, bdf, forex, bdf_corr, missing) = extract_from_rje_file(
                path, correspondant=correspondant, xlsx_path=xlsx_path,
                allowed_directions=allowed_directions,
                allowed_mt_types=allowed_mt_types,
            )
            all_incoming.extend(inc)
            all_outgoing.extend(out)
            all_beaccmcx091.extend(beac)
            all_exc_323201.extend(exc323)
            all_other_exc.extend(other)
            all_bdf.extend(bdf)
            all_forex.extend(forex)
            all_bdf_corr.extend(bdf_corr)
            all_missing['unmapped'].update(missing.get('unmapped', set()))
            all_missing['empty'].update(missing.get('empty', set()))
        except Exception as e:
            logger.error("eastnet: erreur sur le fichier %s: %s", path, e)
    
    return (all_incoming, all_outgoing, all_beaccmcx091, all_exc_323201,
            all_other_exc, all_bdf, all_forex, all_bdf_corr, all_missing)
