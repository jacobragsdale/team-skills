# <repo>: operational context

What an agent needs to check this system in each environment. Only
/dev-context, /dev-verify, and /dev-support read this file. /dev-context
confirmed every fact here, read-only through agent-cli, on <YYYY-MM-DD>.

## Environments

`ctx.py --env <name>` prints an environment as agent-cli flags. Inside a
domain table, a text, number, or boolean is a flag (`--key value`), and a list
names the things that exist there. Keep only the domains this system uses.

```toml
[dev]
url = "<where a person reaches it>"
deploy = "<how a merged change gets here: pipeline and stage>"

[dev.k8s]
cluster = "<the [[k8s.scope]] name>"
namespace = "<namespace>"
deployments = ["<deployment>"]

[dev.dd]
service = "<service tag>"
env = "<env tag>"

[dev.airflow]
instance = "<the [[airflow.instance]] name>"
dags = ["<dag id>"]

[dev.sql]
conn = "<the [[sql.connection]] name>"
tables = ["<schema.table>"]

[qa]
url = "<where a person reaches it>"
deploy = "<how a merged change gets here: pipeline and stage>"

[qa.k8s]
cluster = "<the [[k8s.scope]] name>"
namespace = "<namespace>"
deployments = ["<deployment>"]

[prod]
deploy = "<how a release gets here: pipeline and stage>"

[prod.k8s]
cluster = "<the [[k8s.scope]] name>"
namespace = "<namespace>"
deployments = ["<deployment>"]
```

## Logs

<Where each environment's logs go, and the agent-cli command that finds this
system's lines, such as `agent-cli dd log list --service <service> --env qa
--status error --since 1h`.>

## Jobs

<The scheduled jobs this repository owns: the DAG or job id in each
environment, its schedule, and what a successful run leaves behind.>

## Data

<The databases this repository reads or writes: the tables it owns, and a
read-only query that shows recent activity.>

## Health

<How to tell the system is healthy: the monitors, endpoints, or metrics to
look at, and their normal values.>
