"""Prova do conteúdo aprovado; autorização só compara e lê prazos locais."""
import copy
import hashlib
import json
import math


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def commercial_content(offer):
    # Não inclui relógio do Gate, observação, imagem operacional ou provas privadas.
    return copy.deepcopy({key: offer.get(key) for key in (
        'kind', 'source', 'store', 'product_id', 'source_date', 'entries', 'text',
        'affiliate_generated')})


def build_proof(original, approved, selected=None):
    """Chamado depois do filtro: deadlines pertencem somente à lista final."""
    content = commercial_content(approved)
    if content['store'] == 'Mercado Livre':
        from cupons_mercadolivre import deadline_evidence
        evidence = deadline_evidence(approved)
    elif content['store'] == 'Shopee':
        from cupons_shopee import deadline_metadata
        evidence = {'global': [], 'entries': [deadline_metadata(entry.get('conditions', ''))
                                               for entry in content['entries']]}
    else:
        raise ValueError('Loja fora da prova de cupons capturados')
    body = {'version': 1, 'selection': copy.deepcopy(selected),
            'original_digest': fingerprint(original), 'content': content,
            'content_digest': fingerprint(content),
            'deadlines': {'global': evidence['global'] or [{'kind': 'unspecified', 'ends': []}],
                          'entries': [{'entry_digest': fingerprint(entry), 'deadline': deadline}
                                      for entry, deadline in zip(content['entries'], evidence['entries'])]}}
    # Unknown não pode ser convertido em uma prova aparentemente completa.
    _deadline_ends(body)
    return dict(body, digest=fingerprint(body))


def _deadline_ends(body):
    deadlines = body['deadlines']
    if not isinstance(deadlines, dict) or set(deadlines) != {'global', 'entries'}:
        raise ValueError('Associação de prazos inválida')
    global_deadlines, entries = deadlines['global'], deadlines['entries']
    content_entries = body['content']['entries']
    if (not isinstance(global_deadlines, list) or not global_deadlines
            or not isinstance(entries, list) or not isinstance(content_entries, list)
            or not content_entries or len(entries) != len(content_entries)):
        raise ValueError('Prova incompleta')
    metadata = list(global_deadlines)
    for entry, final_entry in zip(entries, content_entries):
        if (not isinstance(entry, dict) or set(entry) != {'entry_digest', 'deadline'}
                or entry['entry_digest'] != fingerprint(final_entry)):
            raise ValueError('Prazo não pertence à entrada aprovada')
        metadata.append(entry['deadline'])
    ends = []
    for deadline in metadata:
        if (not isinstance(deadline, dict) or set(deadline) != {'kind', 'ends'}
                or deadline['kind'] not in ('explicit', 'unspecified')
                or not isinstance(deadline['ends'], list)
                or (deadline['kind'] == 'explicit') != bool(deadline['ends'])
                or any(type(end) is not int or end <= 0 for end in deadline['ends'])):
            raise ValueError('Prazo ausente, desconhecido ou inválido')
        ends.extend(deadline['ends'])
    return ends


def authorization_status(proof, original, approved, selected, product_id, clock):
    """Não interpreta texto bruto, não filtra entries e não modifica approved."""
    try:
        if (not isinstance(proof, dict) or set(proof) != {
                'version', 'selection', 'original_digest', 'content', 'content_digest', 'deadlines', 'digest'}
                or type(proof['version']) is not int or proof['version'] != 1
                or not isinstance(selected, dict) or proof['selection'] != selected
                or not isinstance(approved, dict)):
            raise ValueError('Prova ou seleção ausente')
        body = {key: value for key, value in proof.items() if key != 'digest'}
        content = commercial_content(approved)
        if (proof['digest'] != fingerprint(body)
                or proof['original_digest'] != fingerprint(original)
                or proof['content'] != content or proof['content_digest'] != fingerprint(content)
                or content['product_id'] != product_id
                or content['kind'] != 'coupon_alert' or content['source'] != 'telegram'
                or content['store'] != original.get('store')):
            raise ValueError('Conteúdo/proveniência não corresponde ao Gate')
        ends = _deadline_ends(body)
        # O relógio vem depois da conferência completa, junto à transição local.
        now = clock()
        if not math.isfinite(now):
            raise ValueError('Relógio inválido')
        return 'CUPOM_EXPIRADO' if any(now >= end for end in ends) else ''
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'CUPOM_VALIDADE_NAO_CONFIRMADA'
