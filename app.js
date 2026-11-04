/* Planejador de Matrícula — versão web.
   A interface conversa com o Python (worker.js) por mensagens. */
'use strict';

const CORES_PERIODO = ['#3B82F6', '#8B5CF6', '#EC4899', '#F97316', '#EAB308',
  '#22C55E', '#14B8A6', '#06B6D4', '#6366F1', '#A855F7', '#64748B'];
const CORES_GRADE = ['#DBEAFE', '#FCE7F3', '#DCFCE7', '#FEF3C7', '#EDE9FE', '#CFFAFE', '#FFE4E6',
  '#E0E7FF', '#ECFCCB', '#FFEDD5', '#F3E8FF', '#CCFBF1'];
const CORES_GRADE_BORDA = ['#3B82F6', '#EC4899', '#22C55E', '#EAB308', '#8B5CF6', '#06B6D4', '#F43F5E',
  '#6366F1', '#84CC16', '#F97316', '#A855F7', '#14B8A6'];
const ESTAGIO = 'ESTÁGIO OBRIGATÓRIO';

const $ = (id) => document.getElementById(id);
const el = (tag, cls, texto) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (texto !== undefined) e.textContent = texto;
  return e;
};
const plural = (n, s, p) => `${n} ${n === 1 ? s : p}`;
const corPeriodo = (p) => (p ? CORES_PERIODO[(p - 1) % CORES_PERIODO.length] : '#94A3B8');

// Preferências do usuário (somente neste navegador)
const pref = {
  ler(chave, padrao) { try { const v = localStorage.getItem('planejador.' + chave); return v === null ? padrao : JSON.parse(v); } catch { return padrao; } },
  gravar(chave, valor) { try { localStorage.setItem('planejador.' + chave, JSON.stringify(valor)); } catch { /* sem armazenamento */ } },
};

// ---------------------------------------------------------------------------
// Comunicação com o worker
// ---------------------------------------------------------------------------
const worker = new Worker('worker.js');
let CURSO = null;
let prontoResolver;
const pronto = new Promise((r) => { prontoResolver = r; });
const pendentes = new Map();
let proximoId = 1;
let ocupado = 0;

worker.onmessage = (ev) => {
  const m = ev.data;
  if (m.tipo === 'progresso') {
    $('statusTexto').textContent = m.texto;
    if (!$('espera').hidden) $('esperaTexto').textContent = m.texto;
  } else if (m.tipo === 'pronto') {
    CURSO = m.curso;
    $('status').classList.add('ok');
    prontoResolver();
  } else if (m.tipo === 'falha') {
    $('statusTexto').textContent = 'Falha ao carregar o planejador';
    alert('Não foi possível carregar o planejador neste navegador.\n\n' + m.erro +
      '\n\nVerifique a conexão com a internet e recarregue a página.');
  } else if (m.tipo === 'resposta') {
    const p = pendentes.get(m.id);
    pendentes.delete(m.id);
    if (!p) return;
    if (m.ok) p.resolve(m.resultado); else p.reject(new Error(m.erro));
  }
};

async function py(cmd, args = [], pdf = null) {
  await pronto;
  const id = proximoId++;
  ocupado++;
  document.body.classList.add('ocupado');
  try {
    return await new Promise((resolve, reject) => {
      pendentes.set(id, { resolve, reject });
      worker.postMessage({ id, cmd, args, pdf }, pdf ? [pdf] : []);
    });
  } finally {
    if (--ocupado === 0) document.body.classList.remove('ocupado');
  }
}

async function comEspera(texto, fn) {
  $('espera').hidden = false;
  $('esperaTexto').textContent = CURSO ? texto : 'Preparando o planejador… (na primeira vez pode levar até 30 segundos)';
  try {
    await pronto;
    $('esperaTexto').textContent = texto;
    return await fn();
  } catch (e) {
    erroInesperado(e);
    return null;
  } finally {
    $('espera').hidden = true;
  }
}

function erroInesperado(e) {
  console.error(e);
  avisar('Ocorreu um erro inesperado: ' + (e && e.message ? e.message.split('\n').slice(-2).join(' ') : e), 'erro', 9000);
}

// ---------------------------------------------------------------------------
// Aviso (toast)
// ---------------------------------------------------------------------------
let timerAviso = null;
function avisar(texto, tipo = 'info', ms = 4500) {
  const a = $('aviso');
  a.textContent = ({ erro: '✖  ', ok: '✔  ', info: 'ℹ  ' }[tipo] || '') + texto;
  a.className = 'aviso ' + tipo;
  a.hidden = false;
  a.style.opacity = 1;
  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => { a.style.opacity = 0; setTimeout(() => { a.hidden = true; }, 320); }, ms);
}
$('aviso').onclick = () => { $('aviso').hidden = true; };

// ---------------------------------------------------------------------------
// Estado da tela
// ---------------------------------------------------------------------------
let ESTADO = null;

function aplicar(res, { novo = [], destacar = [] } = {}) {
  if (!res) return;
  ESTADO = res.estado || res;
  desenhar();
  marcar(novo, 'novo', 800);
  marcar(destacar, 'destaque', 2600);
}

function marcar(nomes, classe, ms) {
  if (!nomes || !nomes.length) return;
  const alvo = new Set(nomes);
  const cartoes = [...document.querySelectorAll('.cartao')].filter((c) => alvo.has(c.dataset.nome));
  cartoes.forEach((c) => c.classList.add(classe));
  setTimeout(() => cartoes.forEach((c) => c.classList.remove(classe)), ms);
}

function cartao(d, modo) {
  const c = el('div', 'cartao ' + modo);
  c.dataset.nome = d.nome;
  c.style.setProperty('--cor', corPeriodo(d.periodo));
  c.title = d.dica || d.titulo;
  c.appendChild(el('div', 'c-titulo', d.titulo));
  const info = el('div', 'c-info');
  if (d.choque) {
    info.textContent = '⚠ choque de horário';
  } else {
    info.appendChild(el('span', '', d.info));
    if (d.sugerida) info.appendChild(el('span', 'pill', '★ sugerida'));
  }
  c.appendChild(info);
  if (d.choque) c.classList.add('choque');
  if (modo === 'disp') c.appendChild(el('span', 'c-acao', '+'));
  if (modo === 'prox') c.appendChild(el('span', 'c-acao', '×'));
  return c;
}

function desenhar() {
  const e = ESTADO;
  $('subtitulo').textContent = `Engenharia Mecânica · UFTM  ·  matrícula em ${e.semestre}`;
  $('tituloDisp').textContent = `Disciplinas disponíveis para ${e.semestre}`;
  $('tituloProx').textContent = `Seu semestre · ${e.semestre}`;

  // 1) disponíveis
  const disp = $('disponiveis');
  disp.replaceChildren();
  for (const grupo of e.disponiveis) {
    const col = el('div', 'coluna');
    const topo = el('div', 'col-topo');
    const b = el('span', 'bolinha'); b.style.background = corPeriodo(grupo.periodo);
    topo.append(b, el('span', 'col-titulo', `${grupo.periodo}º período`));
    const lista = el('div', 'lista');
    for (const d of grupo.disciplinas) {
      const c = cartao(d, 'disp');
      c.onclick = () => acao('adicionar', d.nome);
      lista.appendChild(c);
    }
    col.append(topo, lista);
    disp.appendChild(col);
  }
  if (!e.disponiveis.length) {
    disp.appendChild(el('div', 'vazio-disp', e.tem_disponiveis
      ? 'Todas as disciplinas disponíveis já estão no seu semestre.'
      : 'Não há disciplinas disponíveis para matrícula neste semestre (verifique pré-requisitos e a oferta par/ímpar).'));
  }
  $('btSugestao').hidden = !e.sugestoes.length;
  $('btSugestao').disabled = !e.sugestoes_pendentes.length;

  // 2) seu semestre
  const prox = $('proximo');
  prox.replaceChildren();
  for (const d of e.proximo) {
    const c = cartao(d, 'prox');
    c.onclick = () => acao('remover', d.nome);
    prox.appendChild(c);
  }
  prox.classList.toggle('vazia', !e.proximo.length);
  prox.dataset.vazio = 'Nenhuma disciplina escolhida ainda.\n\nClique nas disciplinas disponíveis ou use a sugestão.';
  const max = CURSO.max_creditos;
  $('creditosProx').innerHTML = `<b>${e.creditos_proximo}</b> de ${max} créditos · ${plural(e.proximo.length, 'disciplina', 'disciplinas')}`;
  $('medidorBarra').style.width = Math.min(100, 100 * e.creditos_proximo / max) + '%';
  $('medidorBarra').classList.toggle('cheio', e.creditos_proximo >= max - 4);
  $('btLimpar').disabled = !e.proximo.length;
  $('btGradeProx').disabled = !e.proximo.length;

  // 3) previsão
  desenharFuturos();
  desenharMax();
  $('chkHorarios').checked = e.horarios;
  $('chkHorarios').disabled = !CURSO.tem_horarios;
  if (e.aviso) avisar(e.aviso, 'erro', 8000);
}

function desenharFuturos() {
  const e = ESTADO;
  const fut = $('futuros');
  fut.replaceChildren();
  const vazio = !e.futuros.length;
  fut.hidden = vazio;
  $('futVazio').hidden = !vazio;
  if (vazio) {
    if (e.proximo.length) {
      $('futVazioTexto').textContent = `Com as disciplinas escolhidas você conclui todas as disciplinas pendentes em ${e.semestre}!`;
      $('btSugestao2').hidden = true;
    } else if (e.tem_disponiveis) {
      $('futVazioTexto').textContent = 'Escolha as disciplinas do seu semestre de matrícula para ver a previsão dos próximos semestres até a formatura.';
      $('btSugestao2').hidden = !e.sugestoes.length;
    } else {
      $('futVazioTexto').textContent = 'Nenhuma disciplina pendente.';
      $('btSugestao2').hidden = true;
    }
  }
  e.futuros.forEach((sem, i) => fut.appendChild(colunaFuturo(sem, i)));
  if (!vazio && e.novo_semestre) fut.appendChild(colunaExtra(e.futuros.length, e.novo_semestre));

  const f = $('formatura');
  f.hidden = !e.formatura;
  if (e.formatura) {
    const total = e.futuros.length + 1;
    f.textContent = vazio ? `Formatura prevista: ${e.formatura}` :
      `Formatura prevista: ${e.formatura} · ${plural(total, 'semestre', 'semestres')}`;
    f.title = `Contando a partir de ${e.semestre} (inclusive)`;
  }
}

function colunaFuturo(sem, i) {
  const col = el('div', 'coluna');
  col.dataset.indice = i;
  const topo = el('div', 'col-topo');
  topo.appendChild(el('span', 'col-titulo', sem.nome));
  const bt = el('button', 'bt mini', '▦ Grade');
  bt.title = 'Ver a grade horária deste semestre';
  bt.disabled = !sem.disciplinas.length;
  bt.onclick = () => abrirGrade(i);
  topo.appendChild(bt);
  const barra = el('div', 'mini-barra');
  const preench = el('div'); preench.style.width = Math.min(100, 100 * sem.creditos / CURSO.max_creditos) + '%';
  barra.appendChild(preench);
  const lista = el('div', 'lista');
  for (const d of sem.disciplinas) {
    const c = cartao(d, 'fut');
    if (d.nome === ESTAGIO) {
      c.classList.add('fixo');
      c.title = 'O estágio obrigatório fica sempre no último semestre do plano.';
    } else {
      c.draggable = true;
      c.addEventListener('dragstart', (ev) => iniciarArraste(ev, d.nome, i, c));
      c.addEventListener('dragend', fimArraste);
      c.onclick = (ev) => abrirMenuMover(ev, d, i);
    }
    lista.appendChild(c);
  }
  lista.classList.toggle('vazia', !sem.disciplinas.length);
  lista.dataset.vazio = 'Arraste disciplinas para cá';
  col.append(topo, el('div', 'col-info', `${plural(sem.disciplinas.length, 'disciplina', 'disciplinas')} · ${sem.creditos} créditos`), barra, lista);
  prepararSoltura(col, i);
  return col;
}

function colunaExtra(i, nome) {
  const col = el('div', 'coluna extra');
  col.dataset.indice = i;
  const t = el('div', 'col-titulo', '＋ Novo semestre');
  t.style.color = 'var(--texto2)';
  col.append(t, el('div', 'col-info', `${nome} · arraste aqui para adiar uma disciplina`));
  prepararSoltura(col, i);
  return col;
}

// ---------------------------------------------------------------------------
// Arrastar e soltar
// ---------------------------------------------------------------------------
let arraste = null;

function iniciarArraste(ev, nome, origem, cartaoEl) {
  arraste = { nome, origem };
  ev.dataTransfer.effectAllowed = 'move';
  ev.dataTransfer.setData('text/plain', nome);
  setTimeout(() => cartaoEl.classList.add('arrastando'), 0);
  py('destinos', [nome, origem]).then((lista) => {
    if (!arraste || arraste.nome !== nome) return;
    for (const d of lista) {
      const col = document.querySelector(`#futuros .coluna[data-indice="${d.indice}"]`);
      if (col) col.classList.add(d.ok ? 'ok' : 'bad');
    }
  }).catch(() => {});
}

function fimArraste() {
  arraste = null;
  document.querySelectorAll('#futuros .coluna').forEach((c) => c.classList.remove('ok', 'bad'));
  document.querySelectorAll('.cartao.arrastando').forEach((c) => c.classList.remove('arrastando'));
}

function prepararSoltura(col, indice) {
  col.addEventListener('dragover', (ev) => { if (arraste) { ev.preventDefault(); ev.dataTransfer.dropEffect = 'move'; } });
  col.addEventListener('drop', (ev) => {
    ev.preventDefault();
    if (!arraste || arraste.origem === indice) { fimArraste(); return; }
    const { nome, origem } = arraste;
    fimArraste();
    moverDisciplina(nome, origem, indice);
  });
}

async function moverDisciplina(nome, origem, destino) {
  try {
    const r = await py('mover', [nome, origem, destino]);
    if (r.erro) {
      avisar(r.erro, 'erro', 6000);
      marcar(r.destacar, 'destaque', 2600);
      return;
    }
    aplicar(r, { novo: r.novo });
  } catch (e) { erroInesperado(e); }
}

// Menu "mover para…" (clique no cartão; útil no celular, onde não há arrastar)
async function abrirMenuMover(ev, d, origem) {
  ev.stopPropagation();
  const menu = $('menuMover');
  menu.replaceChildren(el('div', 'm-titulo', `Mover ${d.titulo} para…`));
  menu.hidden = false;
  posicionarMenu(menu, ev.clientX, ev.clientY);
  const carregando = el('div', 'm-titulo', 'verificando…');
  menu.appendChild(carregando);
  const lista = await py('destinos', [d.nome, origem]);
  carregando.remove();
  for (const dest of lista) {
    const b = el('button');
    b.innerHTML = `${dest.ok ? '✔' : '✖'} ${dest.novo ? dest.nome + ' (novo semestre)' : dest.nome}`;
    if (!dest.ok) {
      b.disabled = true;
      const s = el('small', '', dest.motivo); b.appendChild(s);
    }
    b.onclick = () => { menu.hidden = true; moverDisciplina(d.nome, origem, dest.indice); };
    menu.appendChild(b);
  }
  posicionarMenu(menu, ev.clientX, ev.clientY);
}

function posicionarMenu(menu, x, y) {
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + 'px';
  menu.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + 'px';
}
document.addEventListener('click', (ev) => { if (!$('menuMover').contains(ev.target)) $('menuMover').hidden = true; });

// ---------------------------------------------------------------------------
// Ações
// ---------------------------------------------------------------------------
async function acao(cmd, nome) {
  if (ocupado) return;
  try {
    const r = await py(cmd, [nome]);
    if (r.erro) {
      avisar(r.erro, 'erro', 6000);
      marcar(r.destacar, 'destaque', 2600);
      return;
    }
    aplicar(r, { novo: r.novo });
  } catch (e) { erroInesperado(e); }
}

async function usarSugestao() {
  if (ocupado) return;
  try {
    const r = await py('usar_sugestao');
    aplicar(r, { novo: r.novo });
    avisar(r.novo.length ? `${plural(r.novo.length, 'disciplina sugerida adicionada', 'disciplinas sugeridas adicionadas')}.`
      : 'As disciplinas sugeridas já estão no seu semestre.', r.novo.length ? 'ok' : 'info', 3000);
  } catch (e) { erroInesperado(e); }
}

function desenharMax() {
  const sel = $('selMax');
  sel.replaceChildren();
  for (let v = 6; v <= 9; v++) {
    const b = el('button', ESTADO && ESTADO.max_disciplinas === v ? 'ativo' : '', String(v));
    b.title = 'Limite usado no cálculo da previsão e da sugestão';
    b.onclick = async () => {
      if (ocupado || (ESTADO && ESTADO.max_disciplinas === v)) return;
      pref.gravar('max', v);
      try { aplicar(await py('definir_max', [v])); } catch (e) { erroInesperado(e); }
    };
    sel.appendChild(b);
  }
}

$('chkHorarios').onchange = async (ev) => {
  const ligado = ev.target.checked;
  pref.gravar('horarios', ligado);
  try {
    const r = await py('definir_horarios', [ligado]);
    aplicar(r);
    if (r.removidas.length) avisar('Removidas do seu semestre por choque de horário: ' + r.removidas.join(', ') + '.', 'info', 7000);
  } catch (e) { erroInesperado(e); }
};
$('btSugestao').onclick = usarSugestao;
$('btSugestao2').onclick = usarSugestao;
$('btLimpar').onclick = async () => { if (!ocupado) aplicar(await py('limpar')); };
$('btGradeProx').onclick = () => abrirGrade(null);

// ---------------------------------------------------------------------------
// Importação do histórico
// ---------------------------------------------------------------------------
function escolherArquivo() { $('arquivo').value = ''; $('arquivo').click(); }
$('btImportar').onclick = escolherArquivo;
$('btImportar2').onclick = escolherArquivo;
$('arquivo').onchange = () => { const f = $('arquivo').files[0]; if (f) importar(f); };
$('btManual').onclick = () => comEspera('Preparando…', async () => abrirSituacao(await py('situacao_manual')));

document.addEventListener('dragover', (ev) => {
  if (!arraste && ev.dataTransfer && [...ev.dataTransfer.items].some((i) => i.kind === 'file')) ev.preventDefault();
});
document.addEventListener('drop', (ev) => {
  if (arraste) return;
  const f = [...(ev.dataTransfer?.files || [])].find((x) => /\.pdf$/i.test(x.name) || x.type === 'application/pdf');
  if (f) { ev.preventDefault(); importar(f); }
});

async function importar(arquivo) {
  const dados = await arquivo.arrayBuffer();
  const r = await comEspera('Lendo histórico escolar…', () => py('ler_pdf', ['/tmp/historico.pdf'], dados));
  if (!r) return;
  if (r.erro) {
    if (confirm('Não foi possível ler este arquivo como histórico escolar.\n\n' + r.erro +
      '\n\nVerifique se é o PDF do histórico emitido pelo sistema acadêmico.\n\nDeseja preencher sua situação manualmente?')) {
      comEspera('Preparando…', async () => abrirSituacao(await py('situacao_manual')));
    }
    return;
  }
  abrirSituacao(r);
}

// ---------------------------------------------------------------------------
// Diálogo: situação acadêmica
// ---------------------------------------------------------------------------
const OPCOES_SIT = [['aprovada', 'Aprovada'], ['rm', 'Rend. mín.'], ['pendente', 'Pendente']];
let sitDados = null;
let sitManter = [];

function seletor(opcoes, valor, aoMudar, cls = '') {
  const s = el('div', 'segmentado ' + cls);
  const marcarAtivo = (v) => s.querySelectorAll('button').forEach((b) => b.classList.toggle('ativo', b.dataset.v === v));
  for (const [v, rot] of opcoes) {
    const b = el('button', '', rot);
    b.type = 'button';
    b.dataset.v = v;
    if (v === 'rm') b.title = 'Reprovado com nota ≥ 4,0 (a partir de 2023). Libera as disciplinas que a têm como pré-requisito, mas ela ainda precisa ser cursada.';
    b.onclick = () => { s.dataset.valor = v; marcarAtivo(v); aoMudar && aoMudar(v); };
    s.appendChild(b);
  }
  s.dataset.valor = valor;
  marcarAtivo(valor);
  s.definir = (v) => { s.dataset.valor = v; marcarAtivo(v); };
  return s;
}

function abrirSituacao(dados, manter = []) {
  sitDados = dados;
  sitManter = manter;
  const avisos = $('sitAvisos');
  avisos.replaceChildren(...dados.avisos.map(([tipo, texto]) => el('div', 'banner ' + tipo, texto)));
  const sel = $('sitSemestre');
  sel.replaceChildren(...dados.opcoes.map((o) => { const op = el('option', '', o); op.value = o; return op; }));
  if (dados.semestre && dados.opcoes.includes(dados.semestre)) sel.value = dados.semestre;

  const lista = $('sitLista');
  lista.replaceChildren();
  const grupos = {};
  for (const d of CURSO.disciplinas) (grupos[d.semestre] = grupos[d.semestre] || []).push(d);
  for (const per of Object.keys(grupos).map(Number).sort((a, b) => a - b)) {
    const card = el('div', 'periodo');
    const cab = el('div', 'periodo-cab');
    const bol = el('span', 'bolinha'); bol.style.background = corPeriodo(per);
    const todas = el('button', 'link', 'Marcar todas como aprovadas');
    const nenhuma = el('button', 'link', 'Marcar todas como pendentes');
    cab.append(bol, el('b', '', `${per}º período`), todas, nenhuma);
    card.appendChild(cab);
    const seletores = [];
    for (const d of grupos[per]) {
      const linha = el('div', 'linha-disc');
      linha.append(el('span', 'nome', d.titulo), el('span', 'cr', `${d.creditos} cr`));
      const s = seletor(OPCOES_SIT, dados.estados[d.nome] || 'pendente', atualizarResumo, 'sit');
      s.dataset.nome = d.nome;
      seletores.push(s);
      linha.appendChild(s);
      card.appendChild(linha);
    }
    todas.onclick = () => { seletores.forEach((s) => s.definir('aprovada')); atualizarResumo(); };
    nenhuma.onclick = () => { seletores.forEach((s) => s.definir('pendente')); atualizarResumo(); };
    lista.appendChild(card);
  }
  const est = el('div', 'periodo');
  est.appendChild(el('div', 'periodo-cab')).appendChild(el('b', '', 'Estágio'));
  const linha = el('div', 'linha-disc');
  linha.appendChild(el('span', 'nome', 'Estágio Supervisionado Obrigatório'));
  const sEst = seletor([['aprovada', 'Concluído'], ['pendente', 'Pendente']], dados.estagio ? 'aprovada' : 'pendente', null, 'sit');
  sEst.id = 'sitEstagio';
  linha.appendChild(sEst);
  est.appendChild(linha);
  lista.appendChild(est);
  atualizarResumo();
  $('dlgSituacao').showModal();
  $('dlgSituacao').querySelector('.dlg-corpo').scrollTop = 0;
}

function atualizarResumo() {
  const v = [...document.querySelectorAll('#sitLista .segmentado[data-nome]')].map((s) => s.dataset.valor);
  const c = (x) => v.filter((y) => y === x).length;
  $('sitResumo').innerHTML = `<span style="color:var(--verde)">●</span> ${c('aprovada')} aprovadas &nbsp;
    <span style="color:#D97706">●</span> ${c('rm')} rend. mínimo &nbsp; <span style="color:#64748B">●</span> ${c('pendente')} pendentes`;
}

$('sitCancelar').onclick = () => $('dlgSituacao').close();
$('sitContinuar').onclick = async () => {
  const estados = {};
  document.querySelectorAll('#sitLista .segmentado[data-nome]').forEach((s) => { estados[s.dataset.nome] = s.dataset.valor; });
  const estagio = $('sitEstagio').dataset.valor === 'aprovada';
  const semestre = $('sitSemestre').value;
  $('dlgSituacao').close();
  const max = ESTADO ? ESTADO.max_disciplinas : pref.ler('max', 8);
  const horarios = CURSO.tem_horarios && (ESTADO ? ESTADO.horarios : pref.ler('horarios', true));
  const r = await comEspera('Calculando o planejamento…',
    () => py('novo_plano', [estados, semestre, estagio, max, horarios, sitDados.opcoes, sitManter]));
  if (!r) return;
  $('boasVindas').hidden = true;
  $('planejador').hidden = false;
  $('btEditar').hidden = false;
  $('btExportar').hidden = false;
  aplicar(r);
  if (!r.tem_disponiveis && !r.futuros.length) avisar('Nenhuma disciplina pendente: parabéns, você concluiu a grade!', 'ok', 7000);
};

$('btEditar').onclick = async () => {
  if (!ESTADO) return;
  const d = await py('situacao_atual');
  abrirSituacao(d, ESTADO.proximo.map((x) => x.nome));
};

// ---------------------------------------------------------------------------
// Grade horária
// ---------------------------------------------------------------------------
let GRADE = null;

async function abrirGrade(indice) {
  try {
    GRADE = await py('grade', [indice]);
  } catch (e) { erroInesperado(e); return; }
  $('gradeTitulo').textContent = `Grade horária · ${GRADE.semestre}`;
  $('gradeSub').textContent = `Uma combinação possível de turmas (oferta do semestre ${GRADE.paridade === 1 ? 'ímpar' : 'par'}). ` +
    'Troque a turma de cada disciplina ao lado para ver outras opções.';
  const turmas = $('gradeTurmas');
  turmas.replaceChildren();
  GRADE.com_turma.forEach((d, k) => {
    d.cor = k % CORES_GRADE.length;
    const box = el('div', 'turma');
    const nome = el('div', 't-nome');
    const q = el('span', 'quadrado'); q.style.background = CORES_GRADE_BORDA[d.cor];
    nome.append(q, el('span', '', d.titulo));
    const sel = el('select');
    d.turmas.forEach((t, i) => { const o = el('option', '', t.rotulo); o.value = i; sel.appendChild(o); });
    sel.value = d.escolhida;
    sel.title = sel.selectedOptions[0]?.textContent || '';
    sel.onchange = () => { d.escolhida = Number(sel.value); sel.title = sel.selectedOptions[0].textContent; desenharGrade(); };
    box.append(nome, sel);
    turmas.appendChild(box);
  });
  for (const t of GRADE.sem_turma) {
    const box = el('div', 'turma');
    box.append(el('div', 't-nome', t), el('div', 'dica', 'Sem horário cadastrado'));
    turmas.appendChild(box);
  }
  desenharGrade();
  $('dlgGrade').showModal();
}

function desenharGrade(destino = $('gradeTabela')) {
  const g = destino;
  g.replaceChildren();
  const nAulas = CURSO.aulas.length;
  const dias = CURSO.dias;
  const pausas = CURSO.pausas;
  // linha da grade de cada aula (com linhas extras para intervalos/almoço)
  const linha = [];
  let r = 2;
  g.appendChild(Object.assign(el('div'), { style: 'grid-row:1;grid-column:1' }));
  dias.forEach((d, j) => { const e = el('div', 'dia', d); e.style.gridRow = 1; e.style.gridColumn = j + 2; g.appendChild(e); });
  for (let a = 0; a < nAulas; a++) {
    linha[a] = r;
    const h = el('div', 'hora', CURSO.aulas[a]); h.style.gridRow = r; h.style.gridColumn = 1; g.appendChild(h);
    r++;
    const p = pausas[String(a + 1)];
    if (p && a + 1 < nAulas) {
      const hor = CURSO.horarios_aulas;
      const texto = a + 1 < hor.length ? `${p}  ·  ${hor[a][1]} – ${hor[a + 1][0]}` : p;
      const e = el('div', 'pausa', texto); e.style.gridRow = r; g.appendChild(e);
      r++;
    }
  }
  const ocup = {};
  for (const d of GRADE.com_turma) {
    for (const [dia, aula] of d.turmas[d.escolhida].celulas) (ocup[`${dia},${aula}`] = ocup[`${dia},${aula}`] || []).push(d);
  }
  let choques = 0;
  const igual = (x, y) => x && y && x.length === 1 && y.length === 1 && x[0] === y[0];
  for (let j = 0; j < dias.length; j++) {
    let a = 0;
    while (a < nAulas) {
      const ds = ocup[`${j},${a}`];
      const pos = (e, ini, fim) => { e.style.gridColumn = j + 2; e.style.gridRow = `${linha[ini]} / ${linha[fim] + 1}`; g.appendChild(e); };
      if (!ds) { pos(el('div', 'cel'), a, a); a++; continue; }
      if (ds.length > 1) {
        choques++;
        const e = el('div', 'choque', '⚠ CHOQUE\n' + ds.map((d) => d.titulo).join('\n'));
        e.style.whiteSpace = 'pre-line';
        e.title = ds.map((d) => d.titulo).join(' × ');
        pos(e, a, a); a++; continue;
      }
      let fim = a;
      while (fim + 1 < nAulas && igual(ocup[`${j},${fim + 1}`], ds) && !pausas[String(fim + 1)]) fim++;
      const d = ds[0];
      const e = el('div', 'bloco', d.titulo);
      e.style.setProperty('--cor', CORES_GRADE_BORDA[d.cor]);
      e.style.setProperty('--fundo-cor', CORES_GRADE[d.cor]);
      e.title = `${d.titulo} — turma ${d.escolhida + 1}`;
      pos(e, a, fim);
      a = fim + 1;
    }
  }
  if (destino.id === 'gradeTabela') {
    const st = $('gradeStatus');
    if (choques) {
      st.className = 'banner alerta';
      st.textContent = `⚠ ${plural(choques, 'horário com choque', 'horários com choque')} — escolha outra turma para as disciplinas marcadas em vermelho.`;
    } else {
      st.className = 'banner info';
      st.textContent = GRADE.com_turma.length ? '✔ Sem choque de horário nesta combinação de turmas.'
        : 'Nenhuma disciplina deste semestre tem horário cadastrado.';
    }
  }
}

$('gradeFechar').onclick = () => $('dlgGrade').close();
$('gradeImprimir').onclick = () => {
  const rel = $('relatorio');
  rel.replaceChildren(el('h1', '', `Grade horária · ${GRADE.semestre}`));
  const g = el('div', 'grade');
  rel.appendChild(g);
  desenharGrade(g);
  const lista = el('p');
  lista.textContent = GRADE.com_turma.map((d) => `${d.titulo}: ${d.turmas[d.escolhida].rotulo}`).join('  |  ');
  rel.appendChild(lista);
  imprimir();
};

// ---------------------------------------------------------------------------
// Exportação
// ---------------------------------------------------------------------------
$('btExportar').onclick = () => {
  if (!ESTADO || (!ESTADO.proximo.length && !ESTADO.futuros.length)) {
    avisar('Escolha as disciplinas do seu semestre antes de exportar.', 'info');
    return;
  }
  $('dlgExportar').showModal();
};
$('expFechar').onclick = () => $('dlgExportar').close();
$('expCsv').onclick = async () => {
  const d = await py('exportar');
  const linhas = [['Semestre', 'Disciplina', 'Créditos', 'Período da grade']];
  for (const s of d.semestres) for (const x of s.disciplinas) linhas.push([s.nome, x.titulo, x.creditos, x.periodo]);
  const csv = '﻿' + linhas.map((l) => l.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(';')).join('\r\n');
  const a = el('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = 'planejamento_matricula.csv';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  $('dlgExportar').close();
};
$('expPdf').onclick = async () => {
  const d = await py('exportar');
  const rel = $('relatorio');
  rel.replaceChildren(el('h1', '', 'Planejamento de Matrícula'));
  rel.appendChild(el('p', '', `Engenharia Mecânica · UFTM — gerado em ${new Date().toLocaleDateString('pt-BR')} · ` +
    `Formatura prevista: ${d.formatura || '-'}`));
  for (const s of d.semestres) {
    const cred = s.disciplinas.reduce((t, x) => t + x.creditos, 0);
    rel.appendChild(el('h3', '', `${s.nome} — ${plural(s.disciplinas.length, 'disciplina', 'disciplinas')}, ${cred} créditos`));
    const tab = el('table');
    const cab = el('tr'); cab.append(el('th', '', 'Disciplina'), el('th', '', 'Créditos'));
    tab.appendChild(cab);
    for (const x of s.disciplinas) { const tr = el('tr'); tr.append(el('td', '', x.titulo), el('td', '', x.creditos || '-')); tab.appendChild(tr); }
    rel.appendChild(tab);
  }
  rel.appendChild(el('p', '', 'Os horários consideram a oferta de turmas cadastrada e podem mudar a cada semestre. ' +
    'Confira a oferta oficial antes de se matricular.'));
  $('dlgExportar').close();
  imprimir();
};

function imprimir() {
  // diálogos abertos ficam por cima do conteúdo impresso; fecha temporariamente
  const abertos = [...document.querySelectorAll('dialog[open]')];
  abertos.forEach((d) => d.close());
  setTimeout(() => {
    window.print();
    abertos.forEach((d) => d.showModal());
  }, 50);
}
