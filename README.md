# Planejador de Matrícula — Engenharia Mecânica UFTM

Aplicação web que lê o histórico escolar (PDF) do aluno e mostra:

- as disciplinas que ele pode cursar no próximo semestre (com sugestão);
- a previsão dos semestres até a formatura, respeitando pré-requisitos,
  correquisitos, oferta par/ímpar, limite de créditos e **choque de horário**;
- a grade horária de cada semestre, com escolha de turmas.

**Acesse:** https://pauloflabes.github.io/planejador-matricula/

## Privacidade

Todo o processamento acontece **no navegador do aluno** (Python via
[Pyodide](https://pyodide.org)). O PDF do histórico não é enviado a nenhum
servidor e nada é armazenado.

## Atualizar a oferta de horários (todo semestre)

1. Atualize as planilhas de oferta e gere `curso.json` com `converter_dados.py`
   (projeto desktop).
2. Substitua o arquivo `dados/curso.json` deste repositório — pelo site do
   GitHub (*Add file → Upload files*) ou executando `atualizar_site.bat` na
   pasta do projeto desktop.
3. Em cerca de um minuto o mesmo link já usa os dados novos.

## Estrutura

| Arquivo | Conteúdo |
|---|---|
| `index.html`, `estilo.css`, `app.js` | interface |
| `worker.js` | carrega o Python (Pyodide) e executa os cálculos sem travar a tela |
| `py/planejador_core.py` | leitura do histórico e otimização (scipy / HiGHS) |
| `py/ponte.py` | interface entre o JavaScript e o núcleo |
| `py/pypdf2-3.0.1-py3-none-any.whl` | leitor de PDF |
| `dados/curso.json` | estrutura do curso, equivalências, turmas e horários |
