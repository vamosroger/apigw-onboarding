## What changed

<!-- One or two sentences. -->

## Change request

<!-- CR number, e.g. CHG0012345. Required before the API can be provisioned. -->

## Why

<!-- Context a reviewer needs but can't get from the diff. -->

## Checklist

- [ ] Every CSV row has a valid Name, Domain, and Region ID (not a group code)
- [ ] No row duplicates an API that already exists in AWS
- [ ] `python scripts/validate_requests.py --config-file requests/<file>.csv` passes locally
- [ ] Any new script behaviour has a test
