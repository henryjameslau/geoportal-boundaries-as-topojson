import argparse
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import re
import tempfile

import geopandas as gpd
from pyogrio.errors import DataLayerError, DataSourceError
import requests
from requests.adapters import HTTPAdapter
import topojson as tp
from urllib3.util.retry import Retry


CATALOGUE_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/ArcGIS/rest/services"
)
LOG = logging.getLogger(__name__)


class PipelineError(ValueError):
    pass


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ArcGISClient:
    def __init__(self):
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def close(self):
        self.session.close()

    def get(self, url, **params):
        response = self.session.get(url, params={"f": "json", **params}, timeout=(10, 120))
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise PipelineError(f"{url}: expected a JSON object")
        if "error" in data:
            raise PipelineError(f"{url}: ArcGIS error: {data['error']}")
        return data

    def download(self, url, layer, fields):
        oid = object_id_field(layer)
        limit = layer.get("maxRecordCount")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise PipelineError(f"{url}: missing or invalid maxRecordCount")
        query = url + "/query"
        count_params = {"where": "1=1", "returnCountOnly": "true"}
        expected = self.get(query, **count_params).get("count")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected <= 0:
            raise PipelineError(f"{url}: expected a positive feature count, got {expected}")
        params = {
            "where": "1=1",
            "outFields": ",".join(dict.fromkeys([oid, *fields])),
            "outSR": 4326,
            "returnGeometry": "true",
        }
        capabilities = layer.get("advancedQueryCapabilities") or {}
        features = []
        seen = set()
        envelope = None

        def append_page(page):
            nonlocal envelope
            batch = page.get("features")
            if not isinstance(batch, list) or not batch:
                raise PipelineError(f"{url}: empty or malformed page before completion")
            if envelope is None:
                # The assembled feature set must not trigger GDAL's own pagination.
                envelope = {
                    key: value for key, value in page.items()
                    if key not in ("features", "exceededTransferLimit")
                }
            ids = [feature.get("attributes", {}).get(oid) for feature in batch]
            if any(not isinstance(value, int) or isinstance(value, bool) for value in ids):
                raise PipelineError(f"{url}: missing or invalid object IDs")
            if len(set(ids)) != len(ids) or seen.intersection(ids):
                raise PipelineError(f"{url}: duplicate object IDs or non-progressing page")
            features.extend(batch)
            seen.update(ids)
            if len(features) > expected:
                raise PipelineError(f"{url}: received more features than expected")
            return set(ids)

        if capabilities.get("supportsPagination") and capabilities.get("supportsOrderBy"):
            while True:
                page = self.get(
                    query, **params, resultOffset=len(features),
                    resultRecordCount=limit, orderByFields=f"{oid} ASC",
                )
                append_page(page)
                exceeded = page.get("exceededTransferLimit")
                if exceeded is False or (exceeded is None and len(features) == expected):
                    break
                if len(features) == expected:
                    raise PipelineError(f"{url}: transfer limit contradicts feature count")
        else:
            ids_response = self.get(query, where="1=1", returnIdsOnly="true")
            ids = ids_response.get("objectIds")
            if (
                not isinstance(ids, list)
                or any(not isinstance(value, int) or isinstance(value, bool) for value in ids)
                or len(ids) != expected
                or len(set(ids)) != expected
                or ids_response.get("exceededTransferLimit")
            ):
                raise PipelineError(f"{url}: incomplete or invalid object ID list")
            ids = sorted(ids)

            def fetch_ids(batch_ids):
                page = self.get(query, **params, objectIds=",".join(map(str, batch_ids)))
                if page.get("exceededTransferLimit"):
                    if len(batch_ids) == 1:
                        raise PipelineError(f"{url}: single feature exceeded transfer limit")
                    midpoint = len(batch_ids) // 2
                    fetch_ids(batch_ids[:midpoint])
                    fetch_ids(batch_ids[midpoint:])
                    return
                if append_page(page) != set(batch_ids):
                    raise PipelineError(f"{url}: object ID batch did not match requested IDs")

            for start in range(0, len(ids), limit):
                fetch_ids(ids[start:start + limit])

        if len(features) != expected:
            raise PipelineError(f"{url}: expected {expected} features, received {len(features)}")
        if self.get(query, **count_params).get("count") != expected:
            raise PipelineError(f"{url}: feature count changed during download")
        features.sort(key=lambda feature: feature["attributes"][oid])
        envelope["features"] = features
        # GDAL's ESRIJSON reader handles ArcGIS rings, including holes and multipart polygons.
        try:
            frame = gpd.read_file(io.BytesIO(json.dumps(envelope).encode("utf-8")))
        except (DataLayerError, DataSourceError) as error:
            raise PipelineError(f"{url}: cannot decode ArcGIS geometry: {error}") from error
        if len(frame) != expected or frame.crs is None:
            raise PipelineError(f"{url}: conversion lost features or spatial reference")
        return frame.to_crs("EPSG:4326"), expected


def object_id_field(layer):
    oid = layer.get("objectIdField") or layer.get("objectIdFieldName")
    if oid:
        return oid
    candidates = [
        field["name"] for field in layer.get("fields", [])
        if field.get("type") == "esriFieldTypeOID"
    ]
    if len(candidates) != 1:
        raise PipelineError("Layer must have exactly one object ID field")
    return candidates[0]


def area_fields(layer):
    fields = [field["name"] for field in layer.get("fields", [])]
    codes = [field for field in fields if re.search(r"CD$", field, re.IGNORECASE)]
    names = [field for field in fields if re.search(r"NM$", field, re.IGNORECASE)]
    if len(codes) > 1 or len(names) > 1:
        pairs = [
            (code, name) for code in codes for name in names
            if code[:-2].casefold() == name[:-2].casefold()
            and layer.get("displayField") in (code, name)
        ]
        if len(pairs) == 1:
            return pairs[0]
    if len(codes) != 1 or len(names) != 1:
        raise PipelineError(f"Ambiguous area fields: code={codes}, name={names}")
    if codes[0][:-2].casefold() != names[0][:-2].casefold():
        raise PipelineError(f"Area code/name fields do not match: {codes[0]}, {names[0]}")
    return codes[0], names[0]


def normalize(frame, code, name):
    frame = frame[[code, name, frame.geometry.name]].rename(
        columns={code: "AREACD", name: "AREANM"}
    )
    for column in ("AREACD", "AREANM"):
        if not frame[column].map(
            lambda value: isinstance(value, str) and bool(value.strip())
        ).all():
            raise PipelineError(f"{column} contains missing or empty values")
    if frame["AREACD"].duplicated().any():
        raise PipelineError("AREACD contains duplicate area codes")
    if (
        frame.geometry.isna().any()
        or frame.geometry.is_empty.any()
        or not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all()
        or not frame.geometry.is_valid.all()
    ):
        raise PipelineError("Dataset contains missing, empty, non-polygon or invalid geometry")
    return frame


def publish(client, url, layer, output):
    code, name = area_fields(layer)
    frame, expected = client.download(url, layer, (code, name))
    frame = normalize(frame, code, name)
    topology = json.loads(tp.Topology(frame).to_json())
    geometries = topology.get("objects", {}).get("data", {}).get("geometries", [])
    if topology.get("type") != "Topology" or len(geometries) != expected:
        raise PipelineError("Topology conversion changed the feature count")
    for geometry in geometries:
        if (
            geometry.get("type") not in ("Polygon", "MultiPolygon")
            or not geometry.get("arcs")
            or set(geometry.get("properties", {})) != {"AREACD", "AREANM"}
        ):
            raise PipelineError("Topology has invalid geometry or unexpected properties")
    atomic_json(output, topology)
    return expected, len(geometries)


def select_services(catalogue, requested):
    services = catalogue.get("services")
    if not isinstance(services, list):
        raise PipelineError("Catalogue does not contain a services list")
    selected = {}
    for service in services:
        name = service["name"]
        if service["type"] not in ("FeatureServer", "MapServer"):
            continue
        if not re.search(r"(?:^|_)(?:BUC|BGC|BSC)(?:_|$)", name, re.IGNORECASE):
            continue
        if requested and name not in requested:
            continue
        if Path(name).name != name or name in (".", "..") or "\\" in name:
            raise PipelineError(f"Unsafe service name: {name}")
        if name not in selected or service["type"] == "FeatureServer":
            selected[name] = service
    missing = set(requested) - selected.keys()
    if missing:
        raise PipelineError(f"Requested services not found: {', '.join(sorted(missing))}")
    if not selected:
        raise PipelineError("No matching boundary services found")
    return [selected[name] for name in sorted(selected)]


def discover_layers(client, service):
    url = service["url"].rstrip("/")
    metadata = client.get(url)
    if not isinstance(metadata.get("layers"), list):
        raise PipelineError(f"{url}: service does not contain a layers list")
    layers = []
    for summary in metadata["layers"]:
        layer_id = summary.get("id")
        if not isinstance(layer_id, int) or isinstance(layer_id, bool) or layer_id < 0:
            raise PipelineError(f"{url}: invalid layer ID: {layer_id}")
        layer = client.get(f"{url}/{layer_id}")
        if layer.get("id", layer_id) != layer_id:
            raise PipelineError(f"{url}: layer ID does not match service metadata")
        layer["id"] = layer_id
        capabilities = {value.strip().casefold() for value in layer.get("capabilities", "").split(",")}
        if layer.get("geometryType") == "esriGeometryPolygon" and "query" in capabilities:
            layers.append((f"{url}/{summary['id']}", layer))
        else:
            LOG.warning("Skipping non-queryable or non-polygon layer %s/%s", url, summary["id"])
    return layers


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def refresh(client, services, output_dir, manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"datasets": {}}
    datasets = manifest["datasets"]
    for path in output_dir.glob("*.json"):
        if path.resolve() != manifest_path.resolve():
            datasets.setdefault(path.name, {"output": str(path), "last_success": None})
    for record in datasets.values():
        record["status"] = "not_refreshed"
    manifest.update({
        "started_at": timestamp(), "finished_at": None,
        "failed_datasets_or_services": None, "services": {},
    })
    atomic_json(manifest_path, manifest)
    failures = 0
    for service in services:
        name = service["name"]
        service_record = {"source_url": service["url"], "status": "refreshing", "last_attempt": timestamp()}
        manifest["services"][name] = service_record
        atomic_json(manifest_path, manifest)
        try:
            layers = discover_layers(client, service)
        except (PipelineError, requests.RequestException, OSError) as error:
            LOG.exception("Cannot discover layers for %s", name)
            service_record.update(status="failed", error=str(error))
            failures += 1
            atomic_json(manifest_path, manifest)
            continue
        service_record["status"] = "refreshing" if layers else "skipped"
        if not layers:
            service_record["reason"] = "No queryable polygon layers"
            LOG.warning("%s: no queryable polygon layers", name)
        for url, layer in layers:
            filename = f"{name}.json" if len(layers) == 1 else f"{name}__layer-{layer['id']}.json"
            output = output_dir / filename
            record = datasets.setdefault(filename, {"last_success": None})
            record.update(
                output=str(output), source_url=url, status="refreshing",
                last_attempt=timestamp(), expected_count=None, actual_count=None,
            )
            record.pop("error", None)
            atomic_json(manifest_path, manifest)
            try:
                expected, actual = publish(client, url, layer, output)
            except (PipelineError, requests.RequestException, OSError) as error:
                LOG.exception("Cannot refresh %s", filename)
                record.update(status="failed", error=str(error))
                service_record["status"] = "failed"
                failures += 1
            else:
                record.update(
                    status="success", last_success=timestamp(),
                    expected_count=expected, actual_count=actual,
                )
                LOG.info("%s: published %s complete features", filename, actual)
            atomic_json(manifest_path, manifest)
        if layers and service_record["status"] != "failed":
            service_record["status"] = "success"
        atomic_json(manifest_path, manifest)
    manifest["finished_at"] = timestamp()
    manifest["failed_datasets_or_services"] = failures
    atomic_json(manifest_path, manifest)
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish complete ONS boundary TopoJSON files.")
    parser.add_argument("catalogue", nargs="?", type=Path, default=Path("geoportal.json"))
    parser.add_argument("--fetch-catalogue", action="store_true")
    parser.add_argument("--service", action="append", default=[], help="Exact service name; repeatable")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--manifest", type=Path, default=Path("refresh-manifest.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    client = ArcGISClient()
    try:
        if args.fetch_catalogue:
            catalogue = client.get(CATALOGUE_URL)
        else:
            catalogue = json.loads(args.catalogue.read_text())
        services = select_services(catalogue, args.service)
        if args.fetch_catalogue:
            atomic_json(args.catalogue, catalogue)
        return refresh(client, services, args.output_dir, args.manifest)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
