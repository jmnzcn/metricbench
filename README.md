# MetricBench

[![ci](https://github.com/jmnzcn/metricbench/actions/workflows/ci.yml/badge.svg)](https://github.com/jmnzcn/metricbench/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![dbt + MetricFlow](https://img.shields.io/badge/dbt-MetricFlow-FF694B)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)

Can an AI agent answer business questions about real data correctly? 78 questions about the
[Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) e-commerce dataset (100k real
orders from Brazil), every one with a known answer, asked to four models in three ways:

- **Raw SQL**: the database and its schema. What you get by pointing a model at the warehouse.
- **SQL + glossary**: the same, plus one page with the company's metric definitions.
- **Semantic layer**: the definitions and a dbt + MetricFlow semantic layer as tools, with no SQL.

There is a [results page](https://jmnzcn.github.io/metricbench/) with every answer, the SQL behind it
and the model's own explanation.

## Results

Share of questions answered right (a number counts within 0.1%):

| Model | Raw SQL | SQL + glossary | Semantic layer | Semantic layer, the 64 questions it covers |
|---|---:|---:|---:|---:|
| Claude Opus 5.5 | 42.3% | **100.0%** | 94.9% | **100.0%** |
| Claude Sonnet 5.5 | 47.4% | 97.4% | 94.9% | **100.0%** |
| Claude Haiku 5.5 | 47.4% | 96.2% | 93.6% | 98.4% |
| Gemini 3.8 Flash | 23.1% | 92.3% | 94.9% | **100.0%** |

Cost per question:

| Model | Raw SQL | SQL + glossary | Semantic layer |
|---|---:|---:|---:|
| Claude Opus 5.5 | $0.0124 | $0.0111 | $0.0161 |
| Claude Sonnet 5.5 | $0.0062 | $0.0057 | $0.0082 |
| Claude Haiku 5.5 | $0.0004 | $0.0004 | $0.0005 |
| Gemini 3.8 Flash | $0.0385 | $0.0226 | $0.0131 |

One run per model, November 2026, Claude at effort medium. All 936 answers cost $10.56.

## What I found

**Without definitions, a better model doesn't help.** On raw SQL the four models got 23% to 47%
right, and Opus did worse than Haiku. They rarely wrote broken SQL. They answered a reasonable but
different question, and often said so:

> Total payments of R$8,699,763.05 across 54,011 orders purchased in 2018 gives R$161.07 per order.
> All order statuses are included. *(Opus, average order value; the company's number is R$137.14)*

> Of 52,777 delivered orders bought in 2018 that have a delivery date, 4,944 arrived after the
> estimated delivery timestamp, which is about 9.37%. *(Opus, late delivery rate; it's 7.73% by calendar day)*

Each of those is a defensible choice. None is the company's metric, and nothing in the database says
which one it is.

**One page of definitions fixes most of it.** With the glossary, accuracy went to 92%–100%. That
was the largest effect in the benchmark, for every model.

**The semantic layer makes it consistent.** On the 64 questions it covers, three models got 100% and
Haiku 98.4%: one wrong answer out of 256. With only the glossary, the same questions ranged from
90.6% (Gemini) to 100% (Opus). The weaker the model, the more the semantic layer helped: Gemini
went from 90.6% to 100%, Haiku from 95.3% to 98.4%. Opus gained nothing; it was already perfect
with the glossary.

**Its cost is coverage, and it fails safely.** Four questions need something the semantic layer
doesn't model (distinct sellers, distinct products, items per order, product weight). With SQL and
the glossary the models answered them 16 of 16 times. Through the semantic layer they said they
couldn't, 16 of 16 times, and never made a number up. A semantic layer turns wrong answers into "I
can't answer that", which is the failure you want in finance.

**No model invented an answer** to the 10 questions the data can't answer (profit margin, marketing
spend, returns, customer age...), in any of the 120 attempts.

**Without definitions, Gemini searched instead of answering.** On raw SQL it ran out of its 14
turns on 41 of 78 questions, testing one definition of GMV after another. It took 64 seconds and
$0.039 per question; through the semantic layer, 19 seconds and $0.013.

**Raw SQL is often close but wrong.** How many raw-SQL answers count as right depends a lot on the
tolerance: 40% within 0.1%, 58% within 1%, 74% within 5%. Most misses are near misses, like
including canceled orders, which moves GMV by 0.6%. With definitions the tolerance hardly matters
(95–98%). A finance team won't accept a GMV that's off by 1%, so 0.1% is the score here.

## The traps

They are all in the real data; nothing was planted. How far the obvious query lands from the
company's number:

| Trap | Obvious query | Effect |
|---|---|---|
| A new `customer_id` on every order | count `customer_id` | customers +4.7%, repeat rate 0% instead of 3.0% |
| Items and payments both per order | join both, then sum | GMV +3.9% |
| Freight is not merchandise | `price + freight_value` | GMV +17% |
| Revenue per item vs per order | `avg(price)` | average order value −12% |
| Estimates are dates, deliveries are timestamps | compare the timestamps | late delivery rate +21% |
| `canceled` vs `unavailable` | treat both as canceled | cancellation rate +43% |
| Canceled and unavailable orders | include them | GMV +0.6%, orders +0.6% |
| Several reviews per order | average every review | under 0.1%: this one barely matters |
| Missing and untranslated categories | inner join the translation | drops products |

Each question is tagged with its traps, and the results page breaks accuracy down by tag.

## How the answers are known

Every answerable question has two independent implementations: SQL written by hand against the
raw tables (`metricbench/questions.py`) and the same question through the semantic layer. A test
checks that both agree to nine digits on all 64 questions the layer covers, so the expected answers
don't depend on trusting either one. The four questions outside the layer only have the reference
SQL.

The semantic layer is a dbt project (`olist_dbt/`): staging models, marts (`orders`, `order_items`,
`payments`, `customers`, `products`, `sellers`), six semantic models and 19 metrics.
`metricbench/glossary.md` is the same set of definitions in plain words, and it's what both the
glossary and the semantic-layer agents get, so the only difference between them is the access.

## Run it

```bash
git clone https://github.com/jmnzcn/metricbench && cd metricbench
pip install -e ".[claude]"
metricbench build        # downloads Olist (SHA-256 checked), builds both DuckDB databases
metricbench run --arm semantic --model claude-haiku-5-5
metricbench run --arm sql --backend openai --model gemini-3.8-flash \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai/ --price 0.75,3.75
metricbench report
```

The `openai` backend speaks the chat completions protocol, so it also works with OpenAI, Ollama and
vLLM. Keys come from `ANTHROPIC_API_KEY`, or `METRICBENCH_API_KEY` / `OPENAI_API_KEY`. Runs resume
where they stopped; `--ids` and `--limit` run a subset. `runs/` has every answer from the results
above.

## Limits

- One run per model; 78 questions; one dataset. Differences of one or two questions are noise.
- I wrote the definitions. They're reasonable, not universal: a raw-SQL "wrong" answer is often a
  different but defensible definition, which is the point, but it is not a SQL bug.
- The semantic layer covers what I modeled. Its coverage-gap result depends on that choice.
- Gemini ran through Google's OpenAI-compatible endpoint, with its prices passed by hand.
- 14 turns and 20 tool calls per question. Gemini on raw SQL hit that limit often; with a larger
  budget it would answer more, at a higher cost.
- The dataset is from 2016–2018 and the questions are in English.

## Data

The [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
is CC BY-NC-SA 4.0. This repository doesn't contain it: `metricbench build` downloads it from a
mirror and checks each file against SHA-256 hashes that match two other independent copies.
`benchmark/gold.json` and `runs/` contain aggregates and model answers derived from it, under the
same license. The code is Apache 2.0.

## Resumen en español

MetricBench mide si un agente de IA contesta bien preguntas de negocio sobre datos reales. Son 78
preguntas sobre 100 mil pedidos de Olist, cada una con su respuesta conocida, y cuatro modelos que
acceden a los datos de tres formas: SQL directo, SQL más un glosario de métricas, y una capa
semántica (dbt + MetricFlow).

- **Solo SQL:** los modelos aciertan entre 23% y 47%, y el modelo más grande no ayuda.
- **Con un glosario de una página:** suben a 92%–100%.
- **Con la capa semántica:** aciertan 98%–100% de lo que la capa cubre. Ante lo que no cubre,
  dicen que no pueden responder en vez de inventar.

Ney Fred Jiménez Campos · [LinkedIn](https://www.linkedin.com/in/neyfred/)
