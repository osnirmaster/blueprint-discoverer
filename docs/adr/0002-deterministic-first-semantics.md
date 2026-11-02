# ADR 0002 — Semântica determinística primeiro, LLM como enriquecimento com guardas

**Status:** aceito · 2026-11-02

## Contexto
Inferir propósito e domínio BIAN só com LLM gera respostas variáveis e difíceis de auditar.

## Decisão
1. Classificador determinístico (palavras-chave ponderadas por origem + IDF) gera ranking reprodutível.
2. Claude (`claude-opus-5-5`, saída estruturada, `fallbacks: "default"`) recebe apenas o *evidence pack*,
   a taxonomia fechada e o RULES.md; devolve propósito, objetivos, capacidades e o domínio.
3. Guardas pós-resposta: id fora da taxonomia é descartado, refs inventadas removidas, confiança limitada
   quando diverge do ranking determinístico, `review_required` abaixo de 0.5.
4. Cache endereçado por conteúdo garante a mesma resposta para a mesma evidência.
5. Overrides humanos em `blueprint.yaml` vencem tudo.

## Consequências
Sem credenciais o sistema funciona (modo determinístico). Com LLM, a variabilidade fica restrita ao texto
descritivo; a classificação é auditável e estável entre execuções.
