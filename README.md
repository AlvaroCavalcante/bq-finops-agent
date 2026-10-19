[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)

# BigQuery FinOps Agent

Are you tired of BigQuery queries that look fine in the editor and then show up as a surprise on the bill? A report with a few CTEs, a `SELECT *`, and a date filter that never really prunes the partition?

This project rewrites that SQL for you. Send the query, and any extra context you want the agent to respect. You get back an optimized query, how many bytes it saves, a USD estimate, and the changes it recommends but did not apply.

# Contents

- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Deploy](#deploy)
- [Contribute](#contribute)

# 🤔 How it works <a id="how-it-works"></a>

The agent is a small pipeline built with the [Agent Development Kit (ADK)](https://adk.dev/). Each step has one job. The next step only starts once BigQuery has returned real numbers: which tables the query touches, and how many bytes it scans.

```
input → metadata → optimizer ⇄ validator → report
```

- **Input.** Copies the SQL out of your message, character for character, together with any instruction you wrote next to it. This step does not rewrite the query.
- **Metadata.** Finds the tables with a BigQuery dry-run, then reads partitioning, clustering, and view definitions. It also measures the original query.
- **Optimizer.** Looks for the usual expensive habits — unused columns, filters that do not prune, joins that never reach the final `SELECT` — and rewrites the SQL to read less data.
- **Validator.** Measures the new query and checks that the output columns and types still match. When the rewrite is not good enough, it sends the SQL back to the optimizer for another pass.
- **Report.** Writes the final answer from those measurements. This step does not call a model.

A few guardrails sit around that loop. `UPDATE`, `DELETE`, and DDL are refused. `INSERT` and `MERGE` can be optimized, and they stay on dry-run: the agent never executes them. A live run happens only for a read query (`SELECT` / `WITH`), and only when a table is clustered or a dry-run cannot estimate the bytes. That live run is capped by `BQ_MAX_BYTES_BILLED`.

If you want to know more technical details about the project, and about what each agent is responsible for, please, refer to my Medium article.

# 📝 Prerequisites <a id="prerequisites"></a>

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A Google Cloud project with the BigQuery API and the Vertex AI API enabled
- Permission to create BigQuery jobs and to read the tables you want to optimize

Deploy the agent in the project that can see those tables. Dry-runs, and the occasional live `SELECT`, run with the identity of that project.

# 💾 Installation <a id="installation"></a>

It's recommended to let `uv` create the virtual environment for you, so the ADK and BigQuery libraries stay isolated.

```bash
uv tool install google-agents-cli

git clone https://github.com/AlvaroCavalcante/bq-finops-agent.git
cd bq-finops-agent
uv sync
cp .env.example .env
```

Open `.env` and set `GOOGLE_CLOUD_PROJECT` and `BQ_LOCATION`. The other variables already have sensible defaults. See [Configuration](#configuration).

Then log in with Application Default Credentials, so a local run can talk to BigQuery and Vertex AI:

```bash
gcloud auth application-default login
```

> **Windows:** if the project path contains non-ASCII characters (for example `Área de Trabalho`), point the virtual environment to an ASCII path before `uv sync`:
>
> ```bash
> set UV_PROJECT_ENVIRONMENT=C:\venvs\bq-finops-agent
> ```

# 👨‍🔬 Usage <a id="usage"></a>

To try it in the playground:

```bash
agents-cli playground
```

Or send a single query from the terminal:

```bash
agents-cli run "SELECT * FROM \`bigquery-public-data.samples.shakespeare\` LIMIT 5"
```

The answer is a short report: the rewritten SQL, bytes before and after, the estimated cost, and what the agent chose to leave unchanged.

The same agent can be called from your own pipeline (CLI, API, or [A2A](https://github.com/a2aproject/A2A)), with SQL you already collected somewhere else. Collecting those statements stays outside this repository. The agent only optimizes the query you send.

# ⚙️ Configuration <a id="configuration"></a>

| Variable | What to set | Default |
| --- | --- | --- |
| `GOOGLE_CLOUD_PROJECT` | GCP project that runs the BigQuery jobs | required |
| `BQ_LOCATION` | Location of your datasets (`US`, `EU`, `us-east4`, ...) | `US` |
| `GOOGLE_GENAI_USE_VERTEXAI` | Use Vertex AI | `true` |
| `GOOGLE_CLOUD_LOCATION` | Gemini location. Keep this on `global` | `global` |
| `GEMINI_MODEL` | Model for the metadata, optimizer, and validator steps | `gemini-3.8-flash` |
| `BQ_MAX_BYTES_BILLED` | Cap for a live `SELECT`, in bytes | 50 GiB (`53687091200`) |
| `BQ_ON_DEMAND_USD_PER_TIB` | On-demand price used in the USD estimate | `6.25` |

Two locations show up here, and they are easy to mix up:

- `GOOGLE_CLOUD_LOCATION` is for Gemini. Leave it as `global`.
- `BQ_LOCATION` is for BigQuery jobs. Set it to the region where your datasets live. The sample `.env` uses `us-east4`; if the variable is missing, jobs go to `US`. `BQ_LOCATION=global` is rejected.

# 🚀 Deploy <a id="deploy"></a>

The deploy target is [Agent Runtime](https://adk.dev/deploy/agent-runtime/), through the ADK CLI. The local `.env` is not copied into the container, so pass the same values again on the command line.

Install the CLI once, if you skipped that during [Installation](#installation):

```bash
uv tool install google-agents-cli
```

Before you deploy, pick three values:

1. **Project.** The GCP project that can read the tables. This is `--project`.
2. **Runtime region.** Where the agent itself is deployed. This is `--region`, for example `us-east1`.
3. **`BQ_LOCATION`.** The location of the datasets the queries touch. This is the variable you almost always have to change.

```bash
agents-cli deploy \
  --project YOUR_GCP_PROJECT \
  --region us-east1 \
  --update-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_LOCATION=global,GEMINI_MODEL=gemini-3.8-flash,BQ_LOCATION=US,BQ_MAX_BYTES_BILLED=53687091200,BQ_ON_DEMAND_USD_PER_TIB=6.25"
```

Replace `YOUR_GCP_PROJECT`. Replace `BQ_LOCATION=US` with the location of your data. Change `BQ_ON_DEMAND_USD_PER_TIB` when your contract price is different. Leave `GOOGLE_CLOUD_LOCATION=global` as it is: pointing it at the BigQuery region makes the model call fail.

The identity that runs the agent needs to create jobs and read tables. On the project that owns the data, grant it:

- **BigQuery Job User** (`roles/bigquery.jobUser`)
- **BigQuery Data Viewer** (`roles/bigquery.dataViewer`)

If the tables live in another project, grant Data Viewer there as well.

When the deploy finishes, it prints an engine URL. Send it a query:

```bash
agents-cli run --url ENGINE_URL --mode adk "SELECT * FROM \`bigquery-public-data.samples.shakespeare\` LIMIT 5"
```

A deploy usually takes a few minutes. If the command times out, the work keeps going on the server. Check it with:

```bash
agents-cli deploy --status
```

# 🤝 Contribute <a id="contribute"></a>

Contributions are welcome! Feel free to open a new issue if you have any problem using the agent or if you find a bug.

1. Open an issue to discuss the change you wish to make.
2. Fork the repository and create your feature branch.
3. Commit your changes with clear descriptions.
4. Push to the branch and open a Pull Request.

To run the tests locally:

```bash
uv run pytest tests/unit tests/integration -q
```

## License

Apache 2.0
