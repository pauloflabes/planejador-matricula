# -*- coding: utf-8 -*-
"""
Núcleo do Planejador de Matrícula (sem interface gráfica).

Contém:
  * carregamento da estrutura do curso (data/curso.json);
  * leitura do histórico escolar em PDF;
  * cálculo das disciplinas disponíveis, da sugestão e da previsão dos
    semestres futuros (otimização linear inteira com HiGHS / scipy).

Este módulo não depende de PyQt nem de pandas. As bibliotecas pesadas
(numpy, scipy e PyPDF2) são importadas somente quando necessárias, o que
permite que a janela principal apareça rapidamente.
"""
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------
APROVADA = 'aprovada'
RENDIMENTO_MIN = 'rm'
PENDENTE = 'pendente'

PFC1 = 'PROJETO FINAL DE CURSO I'
PFC2 = 'PROJETO FINAL DE CURSO II'
ESTAGIO = 'ESTÁGIO OBRIGATÓRIO'
ELETIVA1 = 'ELETIVA_1'
ELETIVA2 = 'ELETIVA_2'
CODIGO_ESTAGIO = '3005.000013-8'

MAX_CREDITOS = 36          # máximo de créditos por semestre
CH_ESTAGIO_MIN = 270       # h/a de estágio para considerá-lo concluído
MAX_PERIODO = 11           # maior índice de período considerado na otimização
SITUACOES = 'AP|MA|RF|RN|RP|DS|CA|FL|DC'


def caminho_recurso(relativo):
    """Caminho absoluto de um recurso (funciona no .py e no executável)."""
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relativo)


def nome_exibicao(nome):
    return nome.replace('_', ' ')


def precarregar_bibliotecas():
    """Importa as bibliotecas pesadas (chamado em segundo plano na abertura)."""
    import numpy  # noqa: F401
    from scipy.optimize import linprog  # noqa: F401
    _importar_pdf()


def _importar_pdf():
    try:
        import PyPDF2 as pdf
    except ImportError:  # pragma: no cover - alternativa moderna
        import pypdf as pdf
    return pdf


# --------------------------------------------------------------------------
# Estrutura do curso
# --------------------------------------------------------------------------
class Curso:
    def __init__(self, caminho=None):
        caminho = caminho or caminho_recurso(os.path.join('data', 'curso.json'))
        with open(caminho, encoding='utf-8') as f:
            dados = json.load(f)
        self.disciplinas = dados['disciplinas']
        self.equivalencias = dados['equivalencias']

        self.nomes = [d['nome'] for d in self.disciplinas]
        self.creditos = {d['nome']: d['creditos'] for d in self.disciplinas}
        self.oferta = {d['nome']: list(d['oferta']) for d in self.disciplinas}
        self.semestre = {d['nome']: d['semestre'] for d in self.disciplinas}
        self.codigo = {d['nome']: d['codigo'] for d in self.disciplinas}
        self.pre_base = {}
        self.co_base = {}
        for d in self.disciplinas:
            if d['pre']:
                self.pre_base[d['nome']] = [self.nomes[i] for i in d['pre']]
            if d['co']:
                self.co_base[d['nome']] = [self.nomes[i] for i in d['co']]

        # Turmas: {paridade: {disciplina: [lista de horários de cada turma]}}.
        # Disciplinas sem turma cadastrada não entram na verificação de choque.
        self.turmas = {1: {}, 2: {}}
        for paridade, por_disc in dados.get('turmas', {}).items():
            self.turmas[int(paridade)] = {n: [list(t) for t in ts] for n, ts in por_disc.items() if ts}
        # códigos das turmas (ex.: "T01 - P02"), na mesma ordem de self.turmas
        self.turmas_rotulos = {1: {}, 2: {}}
        for paridade, por_disc in dados.get('turmas_rotulos', {}).items():
            self.turmas_rotulos[int(paridade)] = dict(por_disc)
        self._mascaras = {p: {n: [sum(1 << h for h in t) for t in ts] for n, ts in por.items()}
                          for p, por in self.turmas.items()}

        grade = dados.get('grade', {})
        self.aulas_por_dia = int(grade.get('aulas_por_dia', 12))
        self.dias = list(grade.get('dias', ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta']))
        # horarios_aulas: [[início, término], ...] para cada aula do dia
        self.horarios_aulas = [tuple(h) for h in grade.get('horarios_aulas', [])
                               if isinstance(h, (list, tuple)) and len(h) == 2]
        self.pausas = {int(k): v for k, v in grade.get('pausas', {}).items()}

    def dia_aula(self, horario):
        """Converte o número do horário em (índice do dia, índice da aula)."""
        return (horario - 1) // self.aulas_por_dia, (horario - 1) % self.aulas_por_dia

    def _tem_relogio(self, aula):
        return aula < len(self.horarios_aulas)

    def rotulo_aula(self, aula):
        """Ex.: '07:10 – 08:00' (ou '1ª aula' se o horário não estiver cadastrado)."""
        if self._tem_relogio(aula):
            ini, fim = self.horarios_aulas[aula]
            return f'{ini} – {fim}'
        return f'{aula + 1}ª aula'

    def _faixa(self, ini, fim):
        if self._tem_relogio(ini) and self._tem_relogio(fim):
            return f'{self.horarios_aulas[ini][0]}–{self.horarios_aulas[fim][1]}'
        return f'{ini + 1}ª' if ini == fim else f'{ini + 1}ª–{fim + 1}ª'

    def rotulo_turma(self, nome, paridade, indice):
        """Código da turma (ex.: 'T01 - P02') ou 'Turma N' se não houver."""
        rot = self.turmas_rotulos.get(paridade, {}).get(nome, [])
        if indice < len(rot) and rot[indice]:
            return rot[indice]
        return f'Turma {indice + 1}'

    def aulas_exibidas(self, disciplinas, paridade):
        """Quantas linhas (aulas) a grade deve mostrar: as diurnas e, se alguma
        turma das disciplinas tiver aula à noite, também as noturnas."""
        ultima = 0
        for d in disciplinas:
            for t in self.turmas_de(d, paridade) or []:
                for h in t:
                    ultima = max(ultima, self.dia_aula(h)[1] + 1)
        diurnas = min(12, self.aulas_por_dia)
        return max(diurnas, ultima)

    def descrever_turma(self, horarios):
        """Ex.: 'Ter 07:10–08:50, Qui 07:10–08:50'."""
        por_dia = {}
        for h in horarios:
            d, a = self.dia_aula(h)
            por_dia.setdefault(d, []).append(a)
        partes = []
        for d in sorted(por_dia):
            aulas = sorted(por_dia[d])
            nome = self.dias[d][:3] if d < len(self.dias) else f'Dia {d + 1}'
            faixas, ini = [], aulas[0]
            for anterior, atual in zip(aulas, aulas[1:] + [None]):
                if atual != anterior + 1:
                    faixas.append(self._faixa(ini, anterior))
                    ini = atual
            partes.append(f'{nome} ' + ' e '.join(faixas))
        return ', '.join(partes)

    @property
    def tem_horarios(self):
        return any(self.turmas[p] for p in (1, 2))

    def turmas_de(self, nome, paridade):
        """Lista de turmas (horários) ou None se não houver horário cadastrado."""
        return self.turmas.get(paridade, {}).get(nome)

    def compativel(self, disciplinas, paridade):
        """True se existe uma escolha de turmas sem choque de horário."""
        return self.escolher_turmas(disciplinas, paridade) is not None

    def escolher_turmas(self, disciplinas, paridade):
        """Escolhe uma turma para cada disciplina sem sobreposição de horários.
        Retorna {disciplina: índice da turma} ou None se for impossível.
        (Busca com retrocesso; começa pelas disciplinas com menos turmas.)"""
        mascaras = self._mascaras.get(paridade, {})
        itens = sorted((n for n in disciplinas if n in mascaras), key=lambda n: len(mascaras[n]))
        escolha = {}

        def busca(k, ocupado):
            if k == len(itens):
                return True
            nome = itens[k]
            for i, m in enumerate(mascaras[nome]):
                if not (m & ocupado):
                    escolha[nome] = i
                    if busca(k + 1, ocupado | m):
                        return True
            return False

        return escolha if busca(0, 0) else None

    def grupo_em_choque(self, nova, existentes, paridade):
        """Menor grupo (por eliminação) de 'existentes' que, junto com 'nova',
        não admite turmas sem choque. Lista vazia se não houver choque."""
        grupo = [d for d in existentes if d != nova]
        if self.compativel(grupo + [nova], paridade):
            return []
        for d in list(grupo):
            teste = [x for x in grupo if x != d]
            if not self.compativel(teste + [nova], paridade):
                grupo = teste
        return grupo

    def creditos_de(self, nome):
        return self.creditos.get(nome, 0)

    def ofertada_em(self, nome, paridade):
        """paridade: 1 (semestre ímpar) ou 2 (semestre par)."""
        if nome not in self.oferta:   # Estágio, por exemplo
            return True
        return paridade in self.oferta[nome]

    def texto_oferta(self, nome):
        of = self.oferta.get(nome)
        if of is None:
            return 'Qualquer semestre'
        impar, par = 1 in of, 2 in of
        if impar and par:
            return 'Todos os semestres'
        return 'Somente semestres ímpares' if impar else 'Somente semestres pares'


# --------------------------------------------------------------------------
# Leitura do histórico escolar
# --------------------------------------------------------------------------
class ErroLeitura(Exception):
    pass


@dataclass
class LeituraHistorico:
    aprovadas: list = field(default_factory=list)       # códigos
    com_rendimento_min: list = field(default_factory=list)
    matriculado: bool = False
    estagio_concluido: bool = False
    ano: int = 0
    semestre: int = 0
    ch_obrigatoria_pdf: int = -1
    ch_eletiva_pdf: int = -1
    ch_obrigatoria_lida: int = 0
    ch_eletiva_lida: int = 0
    ch_eletiva_total: int = 0    # aprovadas + matriculadas

    @property
    def carga_confere(self):
        return (self.ch_obrigatoria_lida == self.ch_obrigatoria_pdf and
                self.ch_eletiva_lida == self.ch_eletiva_pdf)


_RE_PERIODOS = re.compile(r'(\d{4}/\d)\s(?: \n|\()')
_RE_SPLIT_PERIODOS = re.compile(r'\d{4}/\d{1}  \n|\d{4}/\d{1} \(')
# A situação vem logo após a nota (ou '-'); exigir um dígito, espaço ou '-'
# antes evita confundir com finais de palavras como "MECÂNICA\n" ou "DRCA\n".
_RE_SITUACAO = re.compile(r'(?<=[\d\s-])(' + SITUACOES + r')[ \t]*(?:\n|$)')
_RE_CODIGO = re.compile(r'\d{4}\.\d{6}-\d{1}')
_RE_SPLIT_CODIGO = re.compile(r'\d{4}\.\d{6}-\d{1} -')
_RE_TURMA = re.compile(r'(P\d{2})|(T\d{2})|(OBR \d)|(OBR\d)|(ELE \d)|(OPT \d)|(ENC \d)')
# Tipo e carga horária. Formato atual: números separados ("OBR 60 54 6 8,7 AP");
# formato antigo: números colados ("OBR 6046140,8RN"), onde só os 2 primeiros são a CH.
_RE_TIPO = re.compile(r'(OBR|ELE|OPT|ENC) ?(\d{2,3})(?=\s+[\d-])|(OBR|ELE|OPT|ENC) ?(\d{2})')
# Os números da linha vêm colados (ex.: "OBR 6046140,8RN" = CH 60, presença 46,
# faltas 14, nota 0,8). A nota é sempre "d,d" imediatamente antes da situação.
# (Nota 10,0 seria lida como 0,0, o que é irrelevante: só a nota de RN é usada.)
_RE_NOTA = re.compile(r'(\d,\d)\s*(?:' + SITUACOES + r')[ \t]*(?:\n|$)')
_RE_CH_OBR = re.compile(r'obrigatórias cursadas \(Aprovadas ou dispensadas por análise curricular\):? \d+ h/a (\d+)')
_RE_CH_ELE = re.compile(r'Eletivas cursadas \(Aprovadas ou dispensadas por análise curricular\):? \d+ h/a (\d+)')
_RE_CH_EST = re.compile(r'Estágio Supervisionado Obrigatório:? \d+ h/a (\d+)')


def ler_historico(caminho, progresso=None):
    """Lê o histórico escolar (PDF) e devolve um LeituraHistorico.

    progresso: função opcional chamada com valores de 0 a 100.
    Lança ErroLeitura se o arquivo não puder ser interpretado.
    """
    pdf = _importar_pdf()
    try:
        with open(caminho, 'rb') as arquivo:
            leitor = pdf.PdfReader(arquivo)
            textos = [p.extract_text() or '' for p in leitor.pages]
    except Exception as e:  # arquivo corrompido, protegido, etc.
        raise ErroLeitura(f'Não foi possível abrir o PDF ({e}).') from e

    if not textos:
        raise ErroLeitura('O PDF não possui páginas.')

    res = LeituraHistorico()
    texto_final = '\n'.join(textos[-2:])   # quadro de carga horária fica no fim

    m = _RE_CH_OBR.search(texto_final)
    if not m:
        raise ErroLeitura('O arquivo não parece ser um histórico escolar da UFTM '
                          '(quadro de carga horária não encontrado).')
    res.ch_obrigatoria_pdf = int(m.group(1))
    m = _RE_CH_ELE.search(texto_final)
    res.ch_eletiva_pdf = int(m.group(1)) if m else 0
    m = _RE_CH_EST.search(texto_final)
    res.estagio_concluido = bool(m and int(m.group(1)) >= CH_ESTAGIO_MIN)

    aprovadas, com_rm = [], []
    # O texto é analisado inteiro (e não página a página) para que um semestre
    # que continua na página seguinte não perca disciplinas.
    for texto in ['\n'.join(textos)]:
        periodos = _RE_PERIODOS.findall(texto)
        trechos_periodo = _RE_SPLIT_PERIODOS.split(texto)[1:]
        for cont1, trecho_periodo in enumerate(trechos_periodo):
            if _RE_SITUACAO.search(trecho_periodo) is None:
                continue                        # semestre trancado
            if cont1 >= len(periodos):
                break
            ano = int(periodos[cont1][:4])
            semestre = int(periodos[cont1][-1])
            codigos = _RE_CODIGO.findall(trecho_periodo)
            trechos = _RE_SPLIT_CODIGO.split(trecho_periodo)[1:]
            for cont2, trecho in enumerate(trechos):
                if cont2 >= len(codigos):
                    break
                m_tipo = _RE_TIPO.search(trecho)
                m_sit = _RE_SITUACAO.search(trecho)
                if m_tipo is None or m_sit is None:
                    continue
                tipo = m_tipo.group(1) or m_tipo.group(3)
                ch = int(m_tipo.group(2) or m_tipo.group(4))
                if ch == 42 and m_tipo.group(4):
                    ch = 420                    # Estágio Supervisionado (formato antigo)
                situacao = m_sit.group(1)
                m_nota = _RE_NOTA.search(trecho)
                nota = float(m_nota.group(1).replace(',', '.')) if m_nota else 0.0

                res.ano, res.semestre = ano, semestre
                codigo = codigos[cont2]
                if situacao in ('AP', 'DS', 'DC'):
                    aprovadas.append(codigo)
                elif situacao == 'MA':
                    aprovadas.append(codigo)
                    res.matriculado = True
                elif situacao == 'RN' and ano >= 2023 and nota >= 4.0:
                    com_rm.append(codigo)

                if situacao in ('AP', 'DS'):
                    if tipo == 'OBR':
                        res.ch_obrigatoria_lida += ch
                    elif tipo == 'ELE':
                        res.ch_eletiva_lida += ch
                if tipo == 'ELE' and situacao == 'MA':
                    res.ch_eletiva_total += ch
    if progresso:
        progresso(100)

    if res.ano == 0:
        raise ErroLeitura('Nenhuma disciplina foi encontrada no histórico.')

    res.ch_eletiva_total += res.ch_eletiva_lida
    res.aprovadas = aprovadas
    res.com_rendimento_min = com_rm
    if CODIGO_ESTAGIO in aprovadas:
        res.estagio_concluido = True
    return res


def situacao_a_partir_da_leitura(curso, leitura):
    """Converte a leitura do histórico em {disciplina: APROVADA/RENDIMENTO_MIN/PENDENTE}."""
    aprovadas = set(leitura.aprovadas)
    com_rm = set(leitura.com_rendimento_min)
    estados = {n: PENDENTE for n in curso.nomes}
    for codigo_real, equivalentes in curso.equivalencias.items():
        nomes = [n for n in curso.nomes if curso.codigo[n] == codigo_real]
        if not nomes:
            continue
        nome = nomes[0]
        # o código atual da disciplina sempre vale, mesmo que não esteja na tabela
        codigos = set(equivalentes) | {codigo_real}
        if codigos & aprovadas:
            estados[nome] = APROVADA
        elif codigos & com_rm:
            estados[nome] = RENDIMENTO_MIN
    ele = leitura.ch_eletiva_total
    if ele >= 120:
        estados[ELETIVA1] = estados[ELETIVA2] = APROVADA
    elif ele > 0:
        estados[ELETIVA1] = APROVADA
    return estados


def semestre_seguinte(ano, semestre, passos=1):
    for _ in range(passos):
        if semestre == 2:
            ano, semestre = ano + 1, 1
        else:
            semestre = 2
    return ano, semestre


def opcoes_semestre_matricula(ano, semestre, quantidade=4):
    """Semestres possíveis de matrícula após o último semestre cursado
    (o regulamento permite no máximo 4 semestres de afastamento)."""
    opcoes = []
    for i in range(1, quantidade + 1):
        a, s = semestre_seguinte(ano, semestre, i)
        opcoes.append(f'{a}/{s}')
    return opcoes


# --------------------------------------------------------------------------
# Planejamento
# --------------------------------------------------------------------------
class Planejamento:
    """Estado do planejamento de um aluno.

    estados: {disciplina: APROVADA | RENDIMENTO_MIN | PENDENTE}
    semestre_matricula: 'AAAA/S'
    """

    def __init__(self, curso, estados, semestre_matricula, estagio_concluido,
                 max_disciplinas=8, considerar_horarios=True):
        self.curso = curso
        # Evitar choque de horário (usa as turmas de oferta_impar/oferta_par).
        self.horarios = bool(considerar_horarios and curso.tem_horarios)
        self.estados = dict(estados)
        self.semestre_matricula = semestre_matricula
        self.ano_matricula = int(semestre_matricula[:4])
        self.paridade_matricula = int(semestre_matricula[-1])
        self.estagio_concluido = estagio_concluido
        self.max_disciplinas = max_disciplinas

        falta = {n: self.estados.get(n, PENDENTE) != APROVADA for n in curso.nomes}
        self.rm = {n: self.estados.get(n) == RENDIMENTO_MIN for n in curso.nomes}
        self.pfc1_pendente = falta.get(PFC1, False)
        self.pfc2_pendente = falta.get(PFC2, False)

        # Disciplinas aprovadas (ou com rendimento mínimo): liberam pré-requisitos.
        self.aprovadas_ou_rm = {n for n in curso.nomes if not falta[n] or self.rm[n]}
        # Na otimização os PFCs são tratados à parte (adicionados ao final).
        liberadas = self.aprovadas_ou_rm | {PFC1, PFC2}

        # Disciplinas que entram na otimização (na ordem do curso).
        self.otimizaveis = [n for n in curso.nomes if falta[n] and n not in (PFC1, PFC2)]

        # Requisitos ainda pendentes (usados nas validações da interface).
        self.pre = {d: [r for r in reqs if r not in self.aprovadas_ou_rm]
                    for d, reqs in curso.pre_base.items()}
        self.co = {d: [r for r in reqs if r not in self.aprovadas_ou_rm]
                   for d, reqs in curso.co_base.items()}

        # Disponíveis no semestre de matrícula -------------------------------
        cand = [n for n in self.otimizaveis if self.paridade_matricula in curso.oferta[n]]
        cand = [n for n in cand if all(p in liberadas for p in curso.pre_base.get(n, []))]
        nomes_cand = set(cand)
        mudou = True
        while mudou:
            mudou = False
            for n in list(cand):
                if not all(c in liberadas or c in nomes_cand for c in curso.co_base.get(n, [])):
                    nomes_cand.discard(n)
                    cand.remove(n)
                    mudou = True
        # PFC II pode ser cursado assim que o PFC I estiver aprovado.
        if (self.pfc2_pendente and not self.pfc1_pendente and
                self.paridade_matricula in curso.oferta.get(PFC2, [1, 2])):
            cand.append(PFC2)
        self.disponiveis = cand

        # Semestres futuros (nomes) -----------------------------------------
        self.nomes_futuros = []
        a, s = self.ano_matricula, self.paridade_matricula
        for _ in range(20):
            a, s = semestre_seguinte(a, s)
            self.nomes_futuros.append(f'{a}/{s}')

        self.proximo = []          # disciplinas escolhidas para o semestre de matrícula
        self.futuros = []          # lista de listas (semestres futuros)
        self.sugestoes = []
        self.aviso = ''

    # ------------------------------------------------------------------
    def disponiveis_por_periodo(self):
        grupos = {}
        for n in self.disponiveis:
            grupos.setdefault(self.curso.semestre[n], []).append(n)
        return dict(sorted(grupos.items()))

    def creditos_proximo(self):
        return sum(self.curso.creditos_de(n) for n in self.proximo)

    # Ações do semestre de matrícula ----------------------------------
    def pode_adicionar(self, nome):
        """Retorna (ok, mensagem)."""
        if nome in self.proximo:
            return False, f'{nome_exibicao(nome)} já está no semestre.'
        total = self.creditos_proximo() + self.curso.creditos_de(nome)
        if total > MAX_CREDITOS:
            return False, (f'Limite de {MAX_CREDITOS} créditos por semestre superado '
                           f'({total} créditos).')
        for req in self.co.get(nome, []):
            if req not in self.proximo:
                return False, (f'{nome_exibicao(req)} é correquisito de {nome_exibicao(nome)}. '
                               f'Adicione {nome_exibicao(req)} primeiro.')
        if self.horarios:
            grupo = self.curso.grupo_em_choque(nome, self.proximo, self.paridade_matricula)
            if grupo:
                return False, self.texto_choque(nome, grupo)
        return True, ''

    @staticmethod
    def texto_choque(nome, grupo):
        nomes = [nome_exibicao(g) for g in grupo]
        if len(nomes) == 1:
            junto = nomes[0]
        else:
            junto = ', '.join(nomes[:-1]) + ' e ' + nomes[-1]
        if len(grupo) == 1:
            return (f'Choque de horário: nenhuma turma de {nome_exibicao(nome)} é compatível '
                    f'com {junto}.')
        return (f'Choque de horário: não há combinação de turmas que permita cursar '
                f'{nome_exibicao(nome)} junto com {junto}.')

    def choque_proximo(self, nome):
        """Grupo do semestre de matrícula que choca com 'nome' (vazio se não houver)."""
        if not self.horarios:
            return []
        return self.curso.grupo_em_choque(nome, self.proximo, self.paridade_matricula)

    def pode_remover(self, nome):
        for outra in self.proximo:
            if nome in self.co.get(outra, []):
                return False, (f'{nome_exibicao(nome)} é correquisito de {nome_exibicao(outra)}. '
                               f'Remova {nome_exibicao(outra)} primeiro.')
        return True, ''

    # Validações de arraste entre semestres futuros --------------------
    def validar_movimento(self, disciplina, destino):
        """Verifica se 'disciplina' pode ir para o semestre futuro de índice
        'destino'. Retorna (ok, mensagem, disciplina_em_conflito)."""
        if disciplina == ESTAGIO:
            return False, 'O estágio obrigatório fica sempre no último semestre do plano.', None
        sem = list(self.futuros)
        while len(sem) <= destino:          # destino = novo semestre ao final
            sem.append([])
        prox = set(self.proximo)
        dn = nome_exibicao(disciplina)

        # Pré-requisitos
        antes = prox.union(*sem[:destino]) if destino else set(prox)
        for req in self.pre.get(disciplina, []):
            if req not in antes:
                return False, f'{nome_exibicao(req)} é pré-requisito de {dn}.', req
        for outra, reqs in self.pre.items():
            if disciplina in reqs:
                for i, lista in enumerate(sem):
                    if outra in lista and destino >= i:
                        return False, f'{dn} é pré-requisito de {nome_exibicao(outra)}.', outra
        # Correquisitos
        ate = prox.union(*sem[:destino + 1])
        for req in self.co.get(disciplina, []):
            if req not in ate:
                return False, f'{nome_exibicao(req)} é correquisito de {dn}.', req
        for outra, reqs in self.co.items():
            if disciplina in reqs:
                for i, lista in enumerate(sem):
                    if outra in lista and destino > i:
                        return False, f'{dn} é correquisito de {nome_exibicao(outra)}.', outra
        # Oferta
        paridade = int(self.nomes_futuros[destino][-1])
        if not self.curso.ofertada_em(disciplina, paridade):
            tipo = 'ímpares' if paridade == 2 else 'pares'
            return False, f'{dn} é ofertada somente em semestres {tipo}.', None
        # Créditos
        total = sum(self.curso.creditos_de(n) for n in sem[destino] if n != disciplina)
        total += self.curso.creditos_de(disciplina)
        if total > MAX_CREDITOS:
            return False, (f'Limite de {MAX_CREDITOS} créditos superado no semestre '
                           f'{self.nomes_futuros[destino]} ao adicionar {dn}.'), None
        # Choque de horário
        if self.horarios:
            outras = [n for n in sem[destino] if n != disciplina]
            grupo = self.curso.grupo_em_choque(disciplina, outras, paridade)
            if grupo:
                return False, self.texto_choque(disciplina, grupo), grupo
        return True, '', None

    def mover(self, disciplina, origem, destino):
        while len(self.futuros) <= destino:
            self.futuros.append([])
        self.futuros[origem].remove(disciplina)
        self.futuros[destino].append(disciplina)
        self._remover_vazios_finais(self.futuros)

    @staticmethod
    def _remover_vazios_finais(semestres):
        while semestres and not semestres[-1]:
            semestres.pop()
        return semestres

    # Cálculos ---------------------------------------------------------
    def calcular_sugestoes(self):
        """Primeiro semestre da solução ótima considerando o semestre de matrícula vazio."""
        escolhas = self._escolha_otima(self.paridade_matricula, [])
        self.sugestoes = list(escolhas[0]) if escolhas else []
        return self.sugestoes

    def recalcular(self, forcar=False):
        """Atualiza a previsão dos semestres futuros a partir de self.proximo.

        forcar: calcula mesmo com o semestre de matrícula vazio (usado quando
        não há nenhuma disciplina disponível para matrícula)."""
        self.aviso = ''
        if not self.proximo and not forcar:
            self.futuros = []
            return self.futuros
        inicio = 2 if self.paridade_matricula == 1 else 1
        escolhas = self._escolha_otima(inicio, self.proximo)
        # O remanejamento pode esvaziar o último semestre; descartá-lo evita
        # empurrar os PFCs e o estágio um semestre além do necessário.
        self._remover_vazios_finais(escolhas)
        self.futuros = self._adicionar_pfcs_e_estagio(escolhas)
        return self.futuros

    def definir_max_disciplinas(self, valor):
        self.max_disciplinas = int(valor)

    def semestre_formatura(self):
        if self.futuros:
            return self.nomes_futuros[len(self.futuros) - 1]
        if self.proximo:
            return self.semestre_matricula
        return None

    def _adicionar_pfcs_e_estagio(self, escolhas):
        escolhas = [list(s) for s in escolhas]
        paridade_inicio = 2 if self.paridade_matricula == 1 else 1

        def cabe(i, nome):
            paridade = paridade_inicio if i % 2 == 0 else 3 - paridade_inicio
            return (not self.horarios or
                    self.curso.compativel(escolhas[i] + [nome], paridade))

        if self.pfc1_pendente:
            if (escolhas and len(escolhas[-1]) < self.max_disciplinas and
                    cabe(len(escolhas) - 1, PFC1)):
                escolhas[-1].append(PFC1)
                escolhas.append([PFC2])
            else:
                # PFC I é pré-requisito do PFC II: semestres consecutivos
                escolhas += [[PFC1], [PFC2]]
        elif self.pfc2_pendente and PFC2 not in self.proximo:
            escolhas.append([PFC2])
        if not self.estagio_concluido:
            if escolhas:
                escolhas[-1].append(ESTAGIO)
            else:
                escolhas.append([ESTAGIO])
        return escolhas

    # ------------------------------------------------------------------
    # Otimização (mesma formulação da versão 2.10)
    # ------------------------------------------------------------------
    def _escolha_otima(self, periodo_inicio, proximo):
        import numpy as np
        from scipy.optimize import linprog

        curso = self.curso
        linhas = self.otimizaveis
        n = len(linhas)
        if n == 0:
            return []
        idx = {nome: i for i, nome in enumerate(linhas)}
        prox = set(proximo)
        falta = np.array([nome not in prox for nome in linhas])
        if not falta.any():
            return []
        oferta = [[0] if nome in prox else curso.oferta[nome] for nome in linhas]
        rm = [self.rm[nome] for nome in linhas]
        n_oferta = sum(len(o) for o in oferta)

        # Turmas (choque de horário) -------------------------------------------
        # Cada variável binária bin_ip (disciplina i no período p) é dividida em
        # variáveis y_ipt (cursa i no período p na turma t), com
        #     soma_t y_ipt = bin_ip
        # e, para cada período p e cada horário-aula h,
        #     soma de y_ipt das turmas que ocupam h <= 1.
        # Isso impede choques entre 2, 3 ou mais disciplinas sem precisar
        # enumerar os grupos em conflito.
        turma_vars = []            # (coluna_bin, período, horários)
        if self.horarios:
            k = n
            for i, of in enumerate(oferta):
                for p in of:
                    if p >= 1:
                        for t in curso.turmas_de(linhas[i], 1 if p % 2 else 2) or []:
                            turma_vars.append((k, p, t))
                    k += 1
        n_turma = len(turma_vars)
        nvars = n + n_oferta + n_turma
        n_bin = n_oferta + n_turma           # variáveis binárias (0/1)
        ligacoes = {}                         # coluna_bin -> colunas y
        ocupacao = {}                         # (período, horário) -> colunas y
        for c, (k, p, horarios) in enumerate(turma_vars):
            col = n + n_oferta + c
            ligacoes.setdefault(k, []).append(col)
            for h in horarios:
                ocupacao.setdefault((p, h), []).append(col)
        ocupacao = [cols for cols in ocupacao.values() if len(cols) > 1]

        # Restrições de pré e correquisitos ---------------------------------
        linhas_pre, linhas_co = [], []
        for i, nome in enumerate(linhas):
            for p in curso.pre_base.get(nome, []):
                j = idx.get(p)
                if j is not None and not rm[j]:
                    linhas_pre.append((i, j))      # x_j - x_i <= -1
            for c in curso.co_base.get(nome, []):
                j = idx.get(c)
                if j is not None and not rm[j]:
                    linhas_co.append((i, j))       # x_j - x_i <= 0
        n_rest = len(linhas_pre) + len(linhas_co)
        A = np.zeros((n_rest + MAX_PERIODO, nvars), dtype=np.int8)
        b = np.zeros((n_rest + MAX_PERIODO, 1), dtype=np.int64)
        r = 0
        for i, j in linhas_pre:
            A[r, i], A[r, j], b[r] = -1, 1, -1
            r += 1
        for i, j in linhas_co:
            A[r, i], A[r, j], b[r] = -1, 1, 0
            r += 1

        # Períodos de oferta: x_i = soma(p * bin_ip) e soma(bin_ip) = 1 -------
        Aeq = np.zeros((2 * n, nvars), dtype=np.int8)
        beq = np.zeros((2 * n, 1), dtype=np.int8)
        k = n
        lista_periodos = []
        for i, of in enumerate(oferta):
            Aeq[i, i] = 1
            for p in of:
                Aeq[i, k] = -p
                Aeq[n + i, k] = 1
                lista_periodos.append(p)
                k += 1
            beq[n + i] = 1

        # Máximo de disciplinas por semestre -----------------------------------
        for p in range(1, MAX_PERIODO + 1):
            cols = [n + c for c, x in enumerate(lista_periodos) if x == p]
            A[r, cols] = 1
            b[r] = self.max_disciplinas
            r += 1

        # Restrições de turma/horário ------------------------------------------
        if n_turma:
            Aliga = np.zeros((len(ligacoes), nvars), dtype=np.int8)
            for linha, (k, cols) in enumerate(ligacoes.items()):
                Aliga[linha, cols] = 1
                Aliga[linha, k] = -1
            Aeq = np.vstack([Aeq, Aliga])
            beq = np.vstack([beq, np.zeros((len(ligacoes), 1), dtype=np.int8)])
            Ahor = np.zeros((len(ocupacao), nvars), dtype=np.int8)
            for linha, cols in enumerate(ocupacao):
                Ahor[linha, cols] = 1
            A = np.vstack([A, Ahor])
            b = np.vstack([b, np.ones((len(ocupacao), 1), dtype=np.int64)])

        # 1ª otimização: minimizar o número de semestres ----------------------
        lb = np.array(n * [periodo_inicio] + n_bin * [0])
        ub = np.array(n * [9 + periodo_inicio] + n_bin * [1])
        lb[:n] = lb[:n] * falta
        ub[:n] = ub[:n] * falta
        bounds = list(zip(lb, ub))
        coefs = np.array(n * [1] + n_bin * [0])
        res1 = linprog(coefs, A_ub=A, b_ub=b, A_eq=Aeq, b_eq=beq, bounds=bounds,
                       integrality=nvars * [1], method='highs')
        if not res1.success:
            self.aviso = ('Não foi possível calcular a previsão com as restrições atuais '
                          f'({res1.message}). Tente aumentar o máximo de disciplinas por semestre.')
            return []

        x1 = np.around(res1.x)
        escolhas1 = self._agrupar(x1[:n], linhas, periodo_inicio)

        # 2ª otimização: aproximar dos semestres ideais da grade ---------------
        sem_curso = np.array([curso.semestre[nome] for nome in linhas])
        desloc = 9 if periodo_inicio == 1 else 8
        ideal = sem_curso - (desloc - len(escolhas1))
        ideal[ideal < 1] = 1
        ideal = ideal * falta

        A2 = np.concatenate((A, np.zeros((A.shape[0], n), dtype=np.int8)), axis=1)
        Aeq2 = np.concatenate((Aeq, np.zeros((Aeq.shape[0], n), dtype=np.int8)), axis=1)
        nvars2 = nvars + n
        Aabs = np.zeros((2 * n, nvars2), dtype=np.int8)
        babs = np.zeros((2 * n, 1), dtype=np.int64)
        r, k = 0, 0
        for i in range(n):
            if falta[i]:
                Aabs[r, i], Aabs[r, nvars + k], babs[r] = 1, -1, ideal[i]
                Aabs[r + 1, i], Aabs[r + 1, nvars + k], babs[r + 1] = -1, -1, -ideal[i]
                k += 1
                r += 2
        A2 = np.vstack([A2, Aabs])
        b2 = np.concatenate((b, babs))
        coefs2 = np.array(nvars * [0] + n * [1])

        ub_max = int(np.around(max(res1.x[:n])))
        escolhas2 = escolhas1
        max_ch = math.inf
        tentativas = 0
        while max_ch > MAX_CREDITOS and tentativas <= MAX_PERIODO:
            tentativas += 1
            lb2 = np.array(list(lb) + n * [0])
            ub2 = np.array(n * [ub_max] + n_bin * [1] + n * [9 + periodo_inicio])
            ub2[:n] = ub2[:n] * falta
            res2 = linprog(coefs2, A_ub=A2, b_ub=b2, A_eq=Aeq2, b_eq=beq,
                           bounds=list(zip(lb2, ub2)), integrality=nvars2 * [1],
                           method='highs')
            if not res2.success:
                escolhas2 = escolhas1
                break
            x2 = np.around(res2.x)
            escolhas2 = self._agrupar(x2[:n], linhas, periodo_inicio)
            max_ch = max(self._ch(s) for s in escolhas2)
            ub_max += 1

        return self._remanejar(escolhas2, periodo_inicio, prox)

    @staticmethod
    def _agrupar(x, linhas, periodo_inicio):
        maximo = int(max(x))
        return [[linhas[i] for i in range(len(linhas)) if x[i] == s]
                for s in range(periodo_inicio, maximo + 1)]

    def _ch(self, disciplinas):
        return sum(self.curso.creditos_de(d) for d in disciplinas)

    def _remanejar(self, escolhas, paridade_inicial, prox):
        """Pós-processamento: antecipa disciplinas para equilibrar a carga."""
        escolhas = [list(s) for s in escolhas]
        n = len(escolhas)
        if n == 0:
            return escolhas
        paridades = [paridade_inicial if i % 2 == 0 else 3 - paridade_inicial for i in range(n)]
        ch = [self._ch(s) for s in escolhas]
        ch_media = math.ceil(sum(ch) / n)
        for i in range(n - 1):
            concluidas = prox.union(*escolhas[:i])
            concluidas_incl = prox.union(*escolhas[:i + 1])
            sair = False
            for j in range(i + 1, n):
                for d in list(escolhas[j]):
                    if (all(r in concluidas for r in self.pre.get(d, [])) and
                            all(r in concluidas_incl for r in self.co.get(d, [])) and
                            paridades[i] in self.curso.oferta[d] and
                            (not self.horarios or
                             self.curso.compativel(escolhas[i] + [d], paridades[i]))):
                        if ch[i] + self.curso.creditos_de(d) <= ch_media:
                            escolhas[i].append(d)
                            escolhas[j].remove(d)
                            ch = [self._ch(s) for s in escolhas]
                    if ch[i] >= ch_media:
                        sair = True
                        break
                if sair:
                    break
        return escolhas
