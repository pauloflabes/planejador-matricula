# -*- coding: utf-8 -*-
"""
Ponte entre a interface web (JavaScript) e o núcleo do planejador.

Todas as funções recebem e devolvem estruturas simples (dict/list/str/int),
convertidas para JSON pelo worker. O estado do aluno fica na variável global
PLANO, que existe apenas no navegador do próprio aluno: nenhum dado do
histórico é enviado a servidor algum.
"""
import json
from datetime import date

import planejador_core as core
from planejador_core import nome_exibicao

CURSO = None
PLANO = None
OPCOES_SEMESTRE = []


# --------------------------------------------------------------------------
def carregar_curso(caminho):
    global CURSO
    CURSO = core.Curso(caminho)
    c = CURSO
    return {
        'disciplinas': [{'nome': n, 'titulo': nome_exibicao(n), 'semestre': c.semestre[n],
                         'creditos': c.creditos[n]} for n in c.nomes],
        'tem_horarios': c.tem_horarios,
        'dias': c.dias,
        'aulas': [c.rotulo_aula(a) for a in range(c.aulas_por_dia)],
        'pausas': {str(k): v for k, v in c.pausas.items()},
        'horarios_aulas': [list(h) for h in c.horarios_aulas],
        'max_creditos': core.MAX_CREDITOS,
    }


def ler_pdf(caminho):
    """Lê o histórico e devolve a situação proposta para o diálogo de conferência."""
    try:
        lei = core.ler_historico(caminho)
    except Exception as e:  # noqa: BLE001 - mensagem vai para a interface
        return {'erro': str(e) if isinstance(e, core.ErroLeitura) else
                f'Não foi possível ler este arquivo como histórico escolar ({e}).'}
    estados = core.situacao_a_partir_da_leitura(CURSO, lei)
    avisos = []
    if not lei.carga_confere:
        avisos.append(['alerta', '⚠ A carga horária lida não confere com a informada no histórico '
                       f'(obrigatórias: {lei.ch_obrigatoria_lida} de {lei.ch_obrigatoria_pdf} h/a; '
                       f'eletivas: {lei.ch_eletiva_lida} de {lei.ch_eletiva_pdf} h/a). '
                       'Revise a situação das disciplinas com atenção.'])
    if lei.matriculado:
        avisos.append(['info', 'ℹ Você está matriculado em algumas disciplinas. Para o planejamento, '
                       'elas foram consideradas APROVADAS — altere se necessário.'])
    return {'estados': estados, 'estagio': lei.estagio_concluido,
            'opcoes': core.opcoes_semestre_matricula(lei.ano, lei.semestre),
            'avisos': avisos}


def situacao_manual():
    hoje = date.today()
    ano, sem = hoje.year, (1 if hoje.month <= 6 else 2)
    return {'estados': {n: core.PENDENTE for n in CURSO.nomes}, 'estagio': False,
            'opcoes': core.opcoes_semestre_matricula(ano, sem),
            'avisos': [['info', 'ℹ Marque as disciplinas que você já concluiu. Use “Marcar todas como '
                        'aprovadas” para agilizar os períodos completos.']]}


def situacao_atual():
    """Dados para reabrir o diálogo de situação (Editar situação)."""
    return {'estados': PLANO.estados, 'estagio': PLANO.estagio_concluido,
            'opcoes': OPCOES_SEMESTRE, 'semestre': PLANO.semestre_matricula, 'avisos': []}


def novo_plano(estados, semestre, estagio, max_disciplinas, horarios, opcoes, manter=()):
    global PLANO, OPCOES_SEMESTRE
    OPCOES_SEMESTRE = list(opcoes)
    PLANO = core.Planejamento(CURSO, estados, semestre, bool(estagio), int(max_disciplinas),
                              considerar_horarios=bool(horarios))
    PLANO.calcular_sugestoes()
    for n in manter:
        if n in PLANO.disponiveis and PLANO.pode_adicionar(n)[0]:
            PLANO.proximo.append(n)
    _recalcular()
    return estado()


# --------------------------------------------------------------------------
def _recalcular():
    PLANO.recalcular(forcar=not PLANO.disponiveis)


def adicionar(nome):
    ok, msg = PLANO.pode_adicionar(nome)
    if not ok:
        return {'erro': msg, 'destacar': PLANO.choque_proximo(nome), 'estado': estado()}
    PLANO.proximo.append(nome)
    _recalcular()
    return {'estado': estado(), 'novo': [nome]}


def remover(nome):
    ok, msg = PLANO.pode_remover(nome)
    if not ok:
        return {'erro': msg, 'estado': estado()}
    PLANO.proximo.remove(nome)
    _recalcular()
    return {'estado': estado(), 'novo': [nome]}


def usar_sugestao():
    p = PLANO
    adicionadas = []
    progresso = True
    while progresso:
        progresso = False
        for n in p.sugestoes:
            if n not in p.proximo and n in p.disponiveis and p.pode_adicionar(n)[0]:
                p.proximo.append(n)
                adicionadas.append(n)
                progresso = True
    if adicionadas:
        _recalcular()
    return {'estado': estado(), 'novo': adicionadas}


def limpar():
    PLANO.proximo = []
    _recalcular()
    return {'estado': estado()}


def definir_max(valor):
    PLANO.definir_max_disciplinas(int(valor))
    PLANO.calcular_sugestoes()
    _recalcular()
    return {'estado': estado()}


def definir_horarios(ligado):
    p = PLANO
    p.horarios = bool(ligado and CURSO.tem_horarios)
    mantidas = []
    for n in p.proximo:
        if not p.horarios or CURSO.compativel(mantidas + [n], p.paridade_matricula):
            mantidas.append(n)
    mudou = True
    while mudou:
        mudou = False
        for n in list(mantidas):
            if any(r not in mantidas for r in p.co.get(n, [])):
                mantidas.remove(n)
                mudou = True
    removidas = [n for n in p.proximo if n not in mantidas]
    p.proximo = mantidas
    p.calcular_sugestoes()
    _recalcular()
    return {'estado': estado(), 'removidas': [nome_exibicao(n) for n in removidas]}


def destinos(disciplina, origem):
    """Para cada semestre futuro (inclusive o 'novo'), se a disciplina pode ir para lá."""
    n = len(PLANO.futuros)
    saida = []
    for k in range(min(n + 1, len(PLANO.nomes_futuros))):
        if k == origem:
            continue
        ok, msg, _ = PLANO.validar_movimento(disciplina, k)
        saida.append({'indice': k, 'nome': PLANO.nomes_futuros[k], 'ok': ok, 'motivo': msg,
                      'novo': k == n})
    return saida


def mover(disciplina, origem, destino):
    ok, msg, conflito = PLANO.validar_movimento(disciplina, destino)
    if not ok:
        if isinstance(conflito, str):
            conflito = [conflito]
        return {'erro': msg, 'destacar': [disciplina] + list(conflito or []), 'estado': estado()}
    PLANO.mover(disciplina, origem, destino)
    return {'estado': estado(), 'novo': [disciplina]}


# --------------------------------------------------------------------------
def _info(nome):
    if nome == core.ESTAGIO:
        return 'Estágio supervisionado'
    cr = CURSO.creditos_de(nome)
    per = CURSO.semestre.get(nome)
    return f'{cr} créditos · {per}º per.' if per else f'{cr} créditos'


def _dica(nome):
    if nome not in CURSO.creditos:
        return nome_exibicao(nome)

    def lista(reqs):
        return '\n'.join(f"  {'✔' if r in PLANO.aprovadas_ou_rm else '•'} {nome_exibicao(r)}"
                         for r in reqs)

    partes = [nome_exibicao(nome),
              f'{CURSO.creditos_de(nome)} créditos · {CURSO.semestre[nome]}º período da grade',
              f'Oferta: {CURSO.texto_oferta(nome)}']
    if CURSO.pre_base.get(nome):
        partes.append('Pré-requisitos:\n' + lista(CURSO.pre_base[nome]))
    if CURSO.co_base.get(nome):
        partes.append('Correquisitos (mesmo semestre ou antes):\n' + lista(CURSO.co_base[nome]))
    if CURSO.tem_horarios:
        turmas = []
        for par, rot in ((1, 'ímpar'), (2, 'par')):
            if par in CURSO.oferta.get(nome, []):
                t = CURSO.turmas_de(nome, par)
                turmas.append(f'{rot}: {len(t)} turma(s)' if t else f'{rot}: sem horário cadastrado')
        if turmas:
            partes.append('Turmas — ' + '; '.join(turmas))
    return '\n'.join(partes)


def _cartao(nome, **extra):
    d = {'nome': nome, 'titulo': nome_exibicao(nome), 'info': _info(nome),
         'periodo': CURSO.semestre.get(nome), 'dica': _dica(nome)}
    d.update(extra)
    return d


def estado():
    p = PLANO
    escolhidas = set(p.proximo)
    disponiveis = []
    for periodo, nomes in p.disponiveis_por_periodo().items():
        cartoes = []
        for n in nomes:
            if n in escolhidas:
                continue
            choque = p.choque_proximo(n)
            c = _cartao(n, sugerida=n in p.sugestoes, choque=bool(choque))
            if choque:
                c['dica'] = '⚠ ' + p.texto_choque(n, choque) + '\n\n' + c['dica']
            cartoes.append(c)
        if cartoes:
            disponiveis.append({'periodo': periodo, 'disciplinas': cartoes})
    futuros = []
    for i, sem in enumerate(p.futuros):
        futuros.append({'nome': p.nomes_futuros[i], 'disciplinas': [_cartao(n) for n in sem],
                        'creditos': sum(CURSO.creditos_de(n) for n in sem)})
    n = len(p.futuros)
    return {
        'semestre': p.semestre_matricula,
        'disponiveis': disponiveis,
        'tem_disponiveis': bool(p.disponiveis),
        'sugestoes': list(p.sugestoes),
        'sugestoes_pendentes': [s for s in p.sugestoes if s not in escolhidas],
        'proximo': [_cartao(n) for n in p.proximo],
        'creditos_proximo': p.creditos_proximo(),
        'futuros': futuros,
        'novo_semestre': p.nomes_futuros[n] if n < len(p.nomes_futuros) else None,
        'formatura': p.semestre_formatura(),
        'aviso': p.aviso,
        'max_disciplinas': p.max_disciplinas,
        'horarios': p.horarios,
    }


# --------------------------------------------------------------------------
def grade(indice):
    """indice None = semestre de matrícula; senão semestre futuro."""
    p = PLANO
    if indice is None:
        disciplinas, nome = list(p.proximo), p.semestre_matricula
    else:
        disciplinas, nome = list(p.futuros[indice]), p.nomes_futuros[indice]
    paridade = int(nome[-1])
    com = [d for d in disciplinas if CURSO.turmas_de(d, paridade)]
    sem = [d for d in disciplinas if d not in com]
    inicial = CURSO.escolher_turmas(com, paridade) or {}
    itens = []
    for d in com:
        turmas = []
        for i, t in enumerate(CURSO.turmas_de(d, paridade)):
            turmas.append({'rotulo': f'Turma {i + 1}  ·  {CURSO.descrever_turma(t)}',
                           'celulas': [list(CURSO.dia_aula(h)) for h in t]})
        itens.append({'nome': d, 'titulo': nome_exibicao(d), 'turmas': turmas,
                      'escolhida': inicial.get(d, 0)})
    return {'semestre': nome, 'paridade': paridade, 'com_turma': itens,
            'sem_turma': [nome_exibicao(d) for d in sem]}


def exportar():
    """Linhas do plano para CSV/impressão."""
    p = PLANO
    sem = [(p.semestre_matricula + ' (matrícula)', p.proximo)]
    sem += [(p.nomes_futuros[i], d) for i, d in enumerate(p.futuros)]
    return {'formatura': p.semestre_formatura(),
            'semestres': [{'nome': n, 'disciplinas': [{'titulo': nome_exibicao(d),
                                                         'creditos': CURSO.creditos_de(d),
                                                         'periodo': CURSO.semestre.get(d, '')}
                                                        for d in ds]} for n, ds in sem]}


def chamar(nome, args_json):
    """Ponto de entrada único usado pelo worker: devolve JSON."""
    args = json.loads(args_json) if args_json else []
    return json.dumps(globals()[nome](*args), ensure_ascii=False)
