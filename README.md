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

The short version: one page of definitions took every model from 12–40% to 91–100%. The semantic layer
added little accuracy on top of that. What it added is that the models stopped disagreeing with each
other, and that a question it can't answer gets "I can't answer that" and not a number.

There is a [results page](https://jmnzcn.github.io/metricbench/) with every answer, the SQL behind it
and the model's own explanation.

## Results

Share of the 68 answerable questions answered right (a number counts within 0.1%), with a 95% interval:

| Model | Raw SQL | SQL + glossary | Semantic layer | Semantic layer, the 64 questions it covers |
|---|---:|---:|---:|---:|
| Claude Opus 5.5 | 33.8% (20–48) | **100.0%** | 94.1% (87–99) | **100.0%** |
| Claude Sonnet 5.5 | 39.7% (26–54) | 97.1% (90–100) | 94.1% (87–99) | **100.0%** |
| Claude Haiku 5.5 | 39.7% (24–56) | 95.6% (89–100) | 92.6% (85–99) | 98.4% |
| Gemini 3.8 Flash | 11.8% (3–23) | 91.2% (85–97) | 94.1% (87–99) | **100.0%** |

Gemini's low numbers are mostly unfinished questions, not wrong ones. On raw SQL it ran out of its 14
turns on 41 of the 68 questions and got 8 of the other 27 right (29.6%). With the glossary it ran out
on 6 and got the other 62 right.

The other 10 questions can't be answered from the data (profit margin, marketing spend, returns,
customer age...). Every model refused all of them in every arm, 120 of 120, so they are left out of
the table: they would add the same ten right answers to every cell. With them in, the raw SQL column
reads 42.3%, 47.4%, 47.4% and 23.1%, which is what the results page shows.

The intervals come from resampling groups of related questions (`gmv_2017`, `gmv_2018` and
`gmv_SP_2018` are one group, since a model that gets GMV wrong misses all three). They say that the
gap between raw SQL and the other two is real, and that the differences between models inside a
column are not: Opus at 34% and Sonnet at 40% are four questions apart.

Cost per question:

| Model | Raw SQL | SQL + glossary | Semantic layer |
|---|---:|---:|---:|
| Claude Opus 5.5 | $0.0124 | $0.0111 | $0.0161 |
| Claude Sonnet 5.5 | $0.0062 | $0.0057 | $0.0082 |
| Claude Haiku 5.5 | $0.0004 | $0.0004 | $0.0005 |
| Gemini 3.8 Flash | $0.0385 | $0.0226 | $0.0131 |

One run per model, November 2026, Claude at effort medium. All 936 answers cost $10.56.
`metricbench analyze` prints the first table from the files in `runs/`; `benchmark/analysis.json`
has its output.

## What I found

**One page of definitions does most of the work.** With the glossary, accuracy went from 12–40% to
91–100%. That was the largest effect in the benchmark, for every model, and it needed no semantic
layer.

**The raw SQL score measures knowing the definitions, not writing SQL.** The models rarely wrote
broken SQL. They answered a reasonable but different question, and often said so:

> Total payments of R$8,699,763.05 across 54,011 orders purchased in 2018 gives R$161.07 per order.
> All order statuses are included. *(Opus, average order value; the company's number is R$137.14)*

> Of 52,777 delivered orders bought in 2018 that have a delivery date, 4,944 arrived after the
> estimated delivery timestamp, which is about 9.37%. *(Opus, late delivery rate; it's 7.73% by calendar day)*

Each of those is a defensible choice. None is the company's metric, and nothing in the database says
which one it is. So "34% right" does not mean the model fails at SQL two times out of three. It means
that two times out of three its reading of the question was not mine.

**How far off the wrong answers are.** Of the 127 wrong raw-SQL answers from the three Claude models:

| Distance from the expected number | Wrong answers | What usually causes it |
|---|---:|---|
| Within 1% | 42 | Counting canceled and unavailable orders (0.6%) |
| 1% to 5% | 33 | Joining items and payments (3.9%), counting `customer_id` (4.7%) |
| Over 5% | 33 | Freight inside GMV (17%), revenue per item, timestamps for late deliveries |
| A table with the wrong rows or order | 19 | |

The causes are the traps in the next section, matched by the size of their effect; I did not check
each answer's SQL one by one. A third of the misses are within 1%, and the explanations I read for
those say the same thing: every order status was counted. Scored at 1% and not 0.1%, raw SQL goes from 31% to 52% over the
four models, and at 5% to 71%. With definitions the tolerance hardly matters (96–98%). A finance team
won't accept a GMV that's off by 1%, so 0.1% is the score here, but the 31% depends on that choice.

**The models miss the same questions.** On raw SQL the three Claude models were all wrong on 33 of
the 68 questions and all right on 17; they split on only 18. A larger model does not help (Opus
scored lowest), because the mistake is not a slip: it is the same reasonable guess, made three times.

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

**Without definitions, Gemini searched instead of answering.** On raw SQL it used up its turns
testing one definition of GMV after another. It took 64 seconds and $0.039 per question; through the
semantic layer, 19 seconds and $0.013.

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
metricbench analyze      # answerable-only accuracy, intervals, how far the misses are; calls no model
```

The `openai` backend speaks the chat completions protocol, so it also works with OpenAI, Ollama and
vLLM. Keys come from `ANTHROPIC_API_KEY`, or `METRICBENCH_API_KEY` / `OPENAI_API_KEY`. Runs resume
where they stopped; `--ids` and `--limit` run a subset. `runs/` has every answer from the results
above.

## Not run yet

Three experiments are set up in the code and have not been run. None of the numbers above come from
them.

- **A fourth arm, `sql+rule`**: raw SQL plus the one convention about which orders count, and nothing
  else. It measures how much of the gap between raw SQL and the glossary is that single convention.
  It gives no exceptions, so the questions about orders placed and canceled orders may go wrong.
  `metricbench run --arm sql+rule --model claude-haiku-5-5`
- **Repeated runs**, to replace the one-run caveat with a spread: `--run 2`, `--run 3`... Each is
  saved in its own file and `metricbench report` shows the lowest and highest run.
- **A larger turn budget for Gemini** on raw SQL, to see how many of its 41 unfinished questions it
  would get right: `--max-turns 30 --max-tool-calls 40`. It is reported as its own row.

`metricbench estimate --arm sql+rule --runs 3` says what a run would cost before starting it, from
what each model spent per question in the saved runs, and `--max-cost 2` stops a run once it has
spent $2. One run of `sql+rule` on the four models comes
to about $4.50, $3.00 of it Gemini.

## Related work

dbt Labs compared text-to-SQL with their Semantic Layer in 2023 and again in
[April 2026](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)
([data](https://github.com/dbt-labs/semantic-layer-llm-benchmarking)), on the ACME Insurance
questions from Juan Sequeda and colleagues at data.world: 11 questions, each run 20 times. They found
the semantic layer more accurate than text-to-SQL, with the gap narrowing as models and data modeling
improved.

MetricBench asks a narrower question on different data, and adds the arm in the middle: SQL with the
metric definitions written down. That arm is where most of the gain is here. It also has questions
the data can't answer and questions the semantic layer doesn't cover, to see how each approach fails.

## Limits

- One run per model; 78 questions; one dataset. Differences of one or two questions are noise.
- I wrote the definitions. They're reasonable, not universal: a raw-SQL "wrong" answer is often a
  different but defensible definition, which is the point, but it is not a SQL bug.
- The semantic layer covers what I modeled. Its coverage-gap result depends on that choice.
- Gemini ran through Google's OpenAI-compatible endpoint, with its prices passed by hand.
- 14 turns and 20 tool calls per question. Gemini on raw SQL hit that limit often; with a larger
  budget it would answer more, at a higher cost.
- The dataset is from 2016–2018 and the questions are in English.
- The 78 questions come from about 65 templates: the same metric asked for different years or states.
  They are not independent, which is why the intervals resample groups of questions.
- The glossary does more than define metrics. It also says that `customer_id` can't count customers,
  which is a fact about the data, so part of its effect is a warning about a trap.
- Olist is a well-known public dataset. The models have very likely seen its schema and analyses of
  it, so raw SQL on a private warehouse could do worse than here.
- The causes in the table of misses are inferred from the size of the error, not read from each query.

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

Sobre las 68 preguntas que los datos permiten responder:

- **Solo SQL:** los modelos aciertan entre 12% y 40%, y el modelo más grande no ayuda. Casi nunca
  escriben SQL inválido: responden con una definición razonable que no es la de la empresa.
- **Con un glosario de una página:** suben a 91%–100%. Es el mayor efecto del estudio.
- **Con la capa semántica:** aciertan 98%–100% de lo que la capa cubre. Ante lo que no cubre,
  indican que no pueden responder y no inventan una cifra.

Ney Fred Jiménez Campos · [LinkedIn](https://www.linkedin.com/in/neyfred/)
