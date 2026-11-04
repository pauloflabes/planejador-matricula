/* Web Worker: roda o Python (Pyodide) fora da linha principal, para a tela
   não travar durante os cálculos. Nenhum dado sai do navegador do aluno. */
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/';
importScripts(PYODIDE + 'pyodide.js');

let pronto = null;

async function iniciar() {
  const avisar = (texto) => postMessage({ tipo: 'progresso', texto });
  avisar('Carregando o Python…');
  const py = await loadPyodide({ indexURL: PYODIDE });
  avisar('Carregando bibliotecas de cálculo (numpy, scipy)…');
  await py.loadPackage(['numpy', 'scipy', 'micropip']);
  avisar('Carregando leitor de PDF…');
  const micropip = py.pyimport('micropip');
  await micropip.install(new URL('py/pypdf2-3.0.1-py3-none-any.whl', self.location).href);

  avisar('Carregando dados do curso…');
  // Os arquivos do planejador são sempre buscados sem cache: quando o professor
  // publica novos dados (curso.json), o mesmo link já passa a usá-los.
  const versao = Date.now();
  for (const nome of ['planejador_core.py', 'ponte.py']) {
    const r = await fetch(`py/${nome}?v=${versao}`, { cache: 'no-store' });
    py.FS.writeFile('/home/pyodide/' + nome, await r.text());
  }
  const dados = await fetch(`dados/curso.json?v=${versao}`, { cache: 'no-store' });
  if (!dados.ok) throw new Error('Não foi possível baixar dados/curso.json');
  py.FS.writeFile('/home/pyodide/curso.json', await dados.text());

  py.runPython('import sys; sys.path.insert(0, "/home/pyodide"); import ponte');
  // pré-aquece a otimização (a primeira chamada ao scipy é a mais lenta)
  py.runPython('import scipy.optimize');
  const curso = JSON.parse(py.runPython('ponte.chamar("carregar_curso", \'["/home/pyodide/curso.json"]\')'));
  return { py, curso };
}

pronto = iniciar();
pronto.then(({ curso }) => postMessage({ tipo: 'pronto', curso }))
      .catch((e) => postMessage({ tipo: 'falha', erro: String(e) }));

onmessage = async (ev) => {
  const { id, cmd, args, pdf } = ev.data;
  try {
    const { py } = await pronto;
    if (pdf) {
      py.FS.writeFile('/tmp/historico.pdf', new Uint8Array(pdf));
    }
    const chamar = py.globals.get('ponte').chamar;
    const resultado = JSON.parse(chamar(cmd, JSON.stringify(args || [])));
    if (pdf) py.FS.unlink('/tmp/historico.pdf');
    postMessage({ tipo: 'resposta', id, ok: true, resultado });
  } catch (e) {
    postMessage({ tipo: 'resposta', id, ok: false, erro: String(e) });
  }
};
