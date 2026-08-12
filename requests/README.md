# Onboarding requests

Each CSV in this directory is an input to `scripts/createapigw.py`. One row
creates one WebSocket API Gateway.

## Format

```csv
Domain,Region,Environment,Pod
pim.acme.com,us-east-1,prod,1
widgets.example.com,us-west-2,production,12
dev.contoso.com,eu-west-1,dev,3
shop.fabrikam.com,ap-southeast-2,development,4
```

Those four rows would create `pdpm1api-pim`, `pdpm12api-widgets`,
`dvpm3api-dev` and `dvpm4api-shop` — one row per accepted Environment spelling.

`example.csv.template` holds this sample. It deliberately does not end in `.csv`
so that committing it never triggers a provisioning run — copy it, don't rename
it in place, and replace every row with your own.

There is no `Name` column — the API name is generated. See below.

| Column   | Meaning                                                              |
| -------- | -------------------------------------------------------------------- |
| `Domain` | Customer domain used to build the `$connect` / `$disconnect` integration URI. Host only — see below. |
| `Region` | AWS region ID such as `us-east-1`. Case-insensitive, checked against an allowlist — see below. |
| `Environment` | `prod`, `production`, `dev` or `development`. Anything else is rejected — it has no `Name` rule. |
| `Pod` | Pod number. **Digits only** — `1`, not `pod1`. |

## Adding a request

Create a **new** file for each batch, named exactly:

```
requests/pim_apigw_<CR>.csv
```

where `<CR>` is the approved change request — `CHG` followed by 7 or 8 digits:

```
requests/pim_apigw_CHG1234567.csv
requests/pim_apigw_CHG12345678.csv
```

Nothing else is permitted in the name: no dates, no requester, no `_v2`. One CR,
one file. Open a PR with it. Merging to `main` triggers the `onboard` workflow,
which detects the added file and provisions every row in it.

Since the CR appears once per file, a change covering several APIs goes in one
file as several rows — not several files.

## A file that does not match is rejected

The workflow fails the run rather than skipping the file, so a request can never
be quietly dropped. Both the run log and the job summary name the offending
file.

| Filename | |
| -------- | - |
| `pim_apigw_CHG1234567.csv` | accepted |
| `pim_apigw_CHG12345678.csv` | accepted — 8-digit CR |
| `pim_apigw_CHG123456.csv` | rejected — six digits |
| `pim_apigw_CHG123456789.csv` | rejected — nine digits |
| `pim_apigw_chg1234567.csv` | rejected — `CHG` must be uppercase |
| `pim_CHG1234567.csv` | rejected — missing `apigw_` |
| `pim-apigw-CHG1234567.csv` | rejected — hyphens, not underscores |
| `pim_apigw_CHG1234567_v2.csv` | rejected — nothing may follow the CR |
| `pim_apigw_CHG1234567.CSV` | rejected — extension must be lowercase |
| `nested/pim_apigw_CHG1234567.csv` | rejected — must sit directly in `requests/` |

To fix a rejected file, rename it and commit the rename. Git records that as a
new file, so the workflow picks it up on merge.

On a manual re-run the CR appears twice — in the filename and in the `cr_number`
input. They must match, or the run stops.

## Never append to an existing file

The workflow only reacts to files **added** by a push. Appending a row to a CSV
that has already been provisioned does nothing — and if you then re-run that
file manually, it re-creates every API in it, duplicates included.

One file per batch, written once, left alone afterwards.

## The Name is generated, not supplied

You do not write the API name. The workflow adds a `Name` column and fills it
with:

```
<prefix><pod>api-<first label of the domain>
```

| Environment | Prefix | Pod | Domain | Name |
| ----------- | ------ | --- | ------ | ---- |
| `prod` | `pdpm` | `1` | `pim.acme.com` | `pdpm1api-pim` |
| `production` | `pdpm` | `12` | `widgets.example.com` | `pdpm12api-widgets` |
| `dev` | `dvpm` | `3` | `dev.contoso.com` | `dvpm3api-dev` |
| `development` | `dvpm` | `4` | `shop.fabrikam.com` | `dvpm4api-shop` |

Only the first label of the domain is used, so the name never contains a dot.
The result is lowercased.

An Environment with no rule is **rejected** — with no `Name` column there is
nothing to fall back on:

```
row 2: Environment 'staging' has no naming rule. Known: dev, development,
       prod, production. Add it to ENVIRONMENT_PREFIXES in scripts/naming.py.
```

Two rows that would generate the same name are also rejected, which happens when
they share an Environment, Pod and first domain label.

## Region must be on the allowlist

Casing does not matter — `US-EAST-1` and `us-east-1` are both accepted, and the
workflow lowercases the column before anything reads it. AWS region IDs are
lowercase, so this is a typo to fix rather than a different region.

The value must be one of the 34 regions in `scripts/regions.py`. Anything else
is rejected, including real AWS regions that are deliberately not on the list:

```
row 2: Region 'us-gov-west-1' is not an allowed region. 34 are allowed
       — see ALLOWED_REGIONS in scripts/regions.py.
```

Group codes such as `use1` are rejected for the same reason — they are not
region IDs. To allow another region, add it to that file.

## Domain is a host, not a URL

Write `example.com`, not `https://example.com`. The script builds the
integration URI as `https://{domain}/XWeb/...`, so a scheme in this column would
produce `https://https://example.com/XWeb/...` — an API that deploys fine and
connects to nothing.

The workflow strips `https://`, `http://`, any other scheme, surrounding
whitespace, and a trailing `/` before provisioning, and logs what it changed.
That fix applies to the runner's copy only; the CSV in git keeps what you wrote.

## Checking a file before you open the PR

```bash
python scripts/validate_requests.py --config-file requests/<your-file>.csv
```

This catches empty fields, disallowed regions, non-numeric pods, unknown
environments, and two rows that would generate the same name. The workflow runs
the same check before calling AWS.
