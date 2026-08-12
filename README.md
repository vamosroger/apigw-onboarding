# apigw-onboarding

Provisions PIM WebSocket API Gateways from CSV request files.

## Layout

```
.github/
  workflows/
    onboard.yml         provisions APIs from newly added request CSVs
requests/               request CSVs (Domain, Region, Environment, Pod)
scripts/
  createapigw.py        creates the WebSocket APIs via boto3
  naming.py             the API naming rule, shared by the scripts below
  regions.py            the allowed AWS regions, shared the same way
  normalize_requests.py cleans Domain and Region, adds the generated Name
  check_existing.py     skips rows whose API name already exists in AWS
  validate_requests.py  checks a request CSV before anything reaches AWS
tests/                  pytest suite for validate_requests
```

## How onboarding works

1. A request arrives with an approved change request (CR) number.
2. A platform engineer creates a **new** CSV under `requests/` holding only that
   change's rows, named `requests/pim_apigw_<CR>.csv` — e.g.
   `requests/pim_apigw_CHG1234567.csv` — and opens a PR. The workflow rejects
   any other filename.
3. Reviewers check the rows. Merging the PR is the approval to provision.
4. On merge, the `onboard` workflow detects the newly added CSV and runs
   `createapigw.py` against it — one matrix job per file.
5. The WebSocket URLs are printed to the run log and rendered as a table in the
   job summary, and the result is commented back on the merged PR.

## Where the WebSocket URLs end up

Three places, all inside the run:

- **The step log** — `createapigw.py` prints each URL as it creates the API, and
  the **Show results** step prints the full list at the end.
- **The job summary** — the same list as a table on the run's front page.
- **The PR comment** — status and a link back to the run.

Nothing is written back to the repository, and no artifact is produced. Logs
follow the repo's retention setting (90 days by default), so copy a URL
somewhere durable if you need it long term.

## Existing APIs are skipped, not duplicated

API Gateway allows two APIs with the same name, so re-provisioning a row would
create a second one indistinguishable from the first except by ApiId.

Before `createapigw.py` runs, `check_existing.py` lists the APIs in each region
named in the CSV. Any row whose name already exists is reported with the
existing API's ApiId and endpoint, then **dropped from the file handed to
`createapigw.py`** — so nothing is created for it.

```
ALREADY EXISTS — SKIPPING 1 row(s)
  pdpm1api-pim  (us-east-1)  [pim.acme.com]
      existing API : ApiId=abc123 protocol=WEBSOCKET endpoint=wss://abc123...
      not creating a new one.
2 row(s) to create, 1 already exist.
```

The same name in a different region is not a match — that row is still created.
If every row already exists, the create step is skipped entirely.

This makes re-running a file safe: rows already provisioned are reported and
left alone, and only genuinely new rows are created. To stop the run on a
collision instead of skipping, add `--fail-on-existing` to the *Skip API names
that already exist* step in `.github/workflows/onboard.yml`.

If the AWS call fails — usually a missing `apigateway:GET` — the step says so
explicitly rather than passing quietly. Rows in that region are then treated as
new, so a permissions problem can still lead to a duplicate.

## Add a new file — never edit an old one

The push trigger uses `git diff --diff-filter=A`, so it only picks up CSVs that
were **added** by the push. Editing an existing CSV triggers nothing at all.

This is deliberate. `createapigw.py` processes every row in the file it is
given, so if appending a row to a provisioned CSV triggered a run, it would
re-create every API already in that file.

## Re-running a file

Use **Run workflow** on the Actions tab. It takes:

| Input | Notes |
| ----- | ----- |
| `csv_file` | Path under `requests/`, e.g. `requests/pim_apigw_CHG1234567.csv` |
| `cr_number` | Approved CR, `CHG` plus 7 or 8 digits |

The `gate` job checks the CR format and only runs on manual dispatch — pushes
skip it, because the merged PR is the approval record. The gate checks the
*format* only; it cannot tell whether a CR is real or approved.

Re-running a file is safe: any row whose API already exists is reported and
skipped, so only genuinely new rows are created. A row that failed partway on an
earlier run may have left a half-built API behind — that name will now count as
existing, so check the console before relying on the skip.

## Setup required before the first real run

| What | Where |
| ---- | ----- |
| `APIGW_AWS_ACCESS_KEY_ID` and `APIGW_AWS_SECRET_ACCESS_KEY` | Settings → Secrets |
| Real team slugs in `.github/CODEOWNERS` | This repo |
| Branch protection on `main` requiring review | Settings → Branches |

The IAM principal needs, on `arn:aws:apigateway:*::/apis*`:

- `apigateway:POST` — create APIs, integrations, routes, stages, deployments
- `apigateway:GET` — list existing APIs for the duplicate-name check

Without `GET` the check logs a warning and the run continues unchecked.

Static keys match the pattern used by the SES onboarding repo. If the account
has a GitHub OIDC provider, prefer `aws-actions/configure-aws-credentials` with
`role-to-assume` and drop the two secrets — short-lived credentials, nothing to
rotate.

## Branch protection matters here

Merging to `main` provisions real infrastructure with no further approval step.
Require a reviewed PR on `main`, or anyone able to push directly can create APIs
in the account.

## Running locally

```bash
pip install -r scripts/requirements-dev.txt
python scripts/validate_requests.py --config-file requests/example.csv.template
pytest
```

Provisioning locally uses the default boto3 credential chain (`AWS_PROFILE`,
SSO cache, instance role — nothing is passed explicitly).
