# ONS boundaries as TopoJSON

Downloads boundaries from the [ONS Geoportal](https://geoportal.statistics.gov.uk/)
and converts them to TopoJSON for the
[ONS data visualisation team's map templates](https://github.com/ONSvisual/maptemplates).
Output properties are `AREACD` (area code) and `AREANM` (English area name).

Find the maps in [outputs](./outputs). Only generalised (`BGC`), super-generalised
(`BSC`) and ultra-generalised (`BUC`) services are selected; full-extent
boundaries are excluded because of their size.

## Run locally

Use Python 3.12, matching the workflow:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python postprocess.py --fetch-catalogue
```

Without `--fetch-catalogue`, the script reads the existing `geoportal.json`.
An alternative catalogue can be supplied as the positional argument.
To refresh one service into a separate directory:

```sh
python postprocess.py geoportal.json \
  --service MSOA_2021_EW_BSC_V3_RUC \
  --output-dir /tmp/ons-boundaries \
  --manifest /tmp/ons-boundaries-manifest.json
```

Repeat `--service` for multiple exact service names. Service names can change
as ONS replaces datasets; use a name from the current catalogue.

## Completeness and publication

- Duplicate catalogue entries prefer FeatureServer over MapServer. MapServer-only
  services are retained, but only queryable polygon layers are downloaded.
  Layer IDs are discovered from service metadata, not assumed to be zero.
- Downloads use each layer's record limit and stable object-ID ordering.
  Pagination follows `exceededTransferLimit`, including short intermediate pages.
  Layers without pagination or ordering support use object-ID batches instead.
- All pages are combined before topology creation, preserving shared boundaries.
  Duplicate IDs, count mismatches, changing counts during download and ArcGIS
  error responses fail the dataset. Count checks are not a transactional snapshot:
  same-count source edits during a download may still require a later refresh.
- A matching `*CD`/`*NM` field pair is required, ignoring case. If several pairs
  exist (for example, joined classification fields), the layer's declared
  `displayField` must uniquely identify the intended pair.
  Welsh-name and unrelated fields are excluded. Ambiguous schemas, empty datasets,
  missing names/codes, duplicate area codes and invalid or missing polygon
  geometry are rejected rather than silently altered.
- Conversion must retain the feature count and expected output properties.
  Validated outputs replace existing files atomically. Failed datasets keep their
  previous files, other services continue, and the command exits non-zero.
- HTTP requests have timeouts and bounded retries for transient failures.

Single-layer services keep the existing `<service>.json` filename. Services with
multiple eligible layers use `<service>__layer-<id>.json` for each layer.
Old files are not automatically deleted when services disappear or layers change.

The generated `refresh-manifest.json` records per-service status and per-output
source URLs, expected/actual feature counts, last attempt, and last successful
refresh. A failed record retains its previous `last_success` timestamp.
`not_refreshed` means an existing file was not attempted in that run
(for example, a retired service or an unselected dataset). Legacy files have no
verified success timestamp until refreshed. An interrupted attempt remains
`refreshing`; check timestamps rather than assuming an old file is current.

## Automation

[The GitHub Actions workflow](./.github/workflows/flat.yml) runs tests on pull
requests and before refreshes. It refreshes daily at 12:06 UTC, on relevant pushes
to `main`, or manually. Python runs directly; Deno and GitHub Flat are no longer
required. Dependency versions (including transitive dependencies) and action
revisions are pinned. To update dependencies, edit `requirements.in`, then
regenerate the lock file with [uv](https://docs.astral.sh/uv/):

```sh
uv pip compile --python-version 3.12 --universal requirements.in --output-file requirements.txt
```

Successful dataset updates and the manifest are committed even if another dataset
fails, and the workflow then reports failure. Repository Actions settings must
allow the workflow to push to the branch; branch protection may require a separate
publication arrangement.

Originally based on
[`pierrotsmnrd`'s Flat/Python example](https://github.com/pierrotsmnrd/flat_data_py_example).
For problems or missing datasets, please open an issue.
