import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import postprocess as pipeline


def layer_metadata(**overrides):
    layer = {
        "id": 7,
        "geometryType": "esriGeometryPolygon",
        "capabilities": "Query",
        "objectIdField": "FID",
        "maxRecordCount": 2,
        "advancedQueryCapabilities": {
            "supportsPagination": True,
            "supportsOrderBy": True,
        },
        "fields": [
            {"name": "FID", "type": "esriFieldTypeOID"},
            {"name": "TESTCD", "type": "esriFieldTypeString"},
            {"name": "TESTNM", "type": "esriFieldTypeString"},
        ],
    }
    return {**layer, **overrides}


def page(ids, exceeded=False):
    return {
        "geometryType": "esriGeometryPolygon",
        "spatialReference": {"wkid": 4326},
        "fields": layer_metadata()["fields"],
        "exceededTransferLimit": exceeded,
        "features": [
            {
                "attributes": {"FID": oid, "TESTCD": f"E{oid:08}", "TESTNM": f"Area {oid}"},
                "geometry": {
                    "rings": [[[oid, 0], [oid, 1], [oid + 1, 1], [oid + 1, 0], [oid, 0]]]
                },
            }
            for oid in ids
        ],
    }


class ScriptedClient(pipeline.ArcGISClient):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **params):
        self.calls.append((url, params))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class DownloadTests(unittest.TestCase):
    def test_short_page_with_transfer_limit_still_fetches_remaining_features(self):
        client = ScriptedClient([
            {"count": 3}, page([1], True), page([2, 3]), {"count": 3},
        ])
        frame, expected = client.download("https://example.test/7", layer_metadata(), ("TESTCD", "TESTNM"))
        self.assertEqual(expected, 3)
        self.assertEqual(frame["TESTCD"].tolist(), ["E00000001", "E00000002", "E00000003"])
        self.assertEqual(frame.crs.to_epsg(), 4326)
        queries = [params for _, params in client.calls if "resultOffset" in params]
        self.assertEqual([query["resultOffset"] for query in queries], [0, 1])
        self.assertTrue(all(query["orderByFields"] == "FID ASC" for query in queries))
        self.assertTrue(all(query["resultRecordCount"] == 2 for query in queries))

    def test_exact_page_boundary_does_not_fetch_an_empty_extra_page(self):
        client = ScriptedClient([{"count": 2}, page([1, 2]), {"count": 2}])
        frame, _ = client.download("https://example.test/7", layer_metadata(), ("TESTCD", "TESTNM"))
        self.assertEqual(len(frame), 2)
        self.assertEqual(len(client.calls), 3)

    def test_absent_transfer_flag_uses_verified_count(self):
        first, second = page([1, 2]), page([3])
        first.pop("exceededTransferLimit")
        second.pop("exceededTransferLimit")
        client = ScriptedClient([{"count": 3}, first, second, {"count": 3}])
        frame, _ = client.download("https://example.test/7", layer_metadata(), ("TESTCD", "TESTNM"))
        self.assertEqual(len(frame), 3)

    def test_incomplete_duplicate_and_changing_results_are_rejected(self):
        cases = {
            "duplicate": ([{"count": 3}, page([1, 2], True), page([2])], "duplicate"),
            "short": ([{"count": 3}, page([1, 2])], "expected 3"),
            "empty": ([{"count": 3}, page([1, 2], True), page([])], "empty"),
            "changed": ([{"count": 2}, page([1, 2]), {"count": 3}], "changed"),
            "zero": ([{"count": 0}], "positive"),
        }
        for label, (responses, error) in cases.items():
            with self.subTest(label=label):
                client = ScriptedClient(responses)
                with self.assertRaisesRegex(pipeline.PipelineError, error):
                    client.download("https://example.test/7", layer_metadata(), ("TESTCD", "TESTNM"))

    def test_id_batches_when_pagination_or_ordering_is_unavailable(self):
        for capabilities in ({}, {"supportsPagination": True, "supportsOrderBy": False}):
            with self.subTest(capabilities=capabilities):
                client = ScriptedClient([
                    {"count": 3}, {"objectIds": [3, 1, 2]},
                    page([2, 1]), page([3]), {"count": 3},
                ])
                frame, _ = client.download(
                    "https://example.test/7",
                    layer_metadata(advancedQueryCapabilities=capabilities),
                    ("TESTCD", "TESTNM"),
                )
                self.assertEqual(len(frame), 3)
                self.assertEqual(
                    frame["TESTCD"].tolist(), ["E00000001", "E00000002", "E00000003"]
                )
                queries = [params for _, params in client.calls if "objectIds" in params]
                self.assertEqual([query["objectIds"] for query in queries], ["1,2", "3"])
                self.assertFalse(any("resultOffset" in params for _, params in client.calls))

    def test_incomplete_id_lists_and_mismatched_batches_are_rejected(self):
        for responses in (
            [{"count": 2}, {"objectIds": [1]}],
            [{"count": 2}, {"objectIds": [1, 1]}],
            [{"count": 2}, {"objectIds": [1, 2]}, page([1, 3])],
        ):
            with self.subTest(responses=responses):
                client = ScriptedClient(responses)
                with self.assertRaises(pipeline.PipelineError):
                    client.download(
                        "https://example.test/7",
                        layer_metadata(advancedQueryCapabilities={}),
                        ("TESTCD", "TESTNM"),
                    )

    def test_id_batches_split_when_server_applies_a_lower_transfer_limit(self):
        client = ScriptedClient([
            {"count": 2}, {"objectIds": [1, 2]},
            page([1], True), page([1]), page([2]), {"count": 2},
        ])
        frame, _ = client.download(
            "https://example.test/7",
            layer_metadata(advancedQueryCapabilities={}),
            ("TESTCD", "TESTNM"),
        )
        self.assertEqual(len(frame), 2)
        self.assertEqual(
            [params["objectIds"] for _, params in client.calls if "objectIds" in params],
            ["1,2", "1", "2"],
        )


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.client = pipeline.ArcGISClient()
        self.addCleanup(self.client.close)

    def test_arcgis_error_in_http_200_response_is_not_success(self):
        response = Mock()
        response.json.return_value = {"error": {"code": 400, "message": "Invalid URL"}}
        with patch.object(self.client.session, "get", return_value=response):
            with self.assertRaisesRegex(pipeline.PipelineError, "Invalid URL"):
                self.client.get("https://example.test/7")

    def test_http_errors_propagate(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("503")
        with patch.object(self.client.session, "get", return_value=response):
            with self.assertRaises(requests.HTTPError):
                self.client.get("https://example.test/7")


class DiscoveryTests(unittest.TestCase):
    def test_duplicate_services_prefer_feature_server_without_losing_map_only_services(self):
        def service(name, kind):
            return {"name": name, "type": kind, "url": f"https://example.test/{name}/{kind}"}
        catalogue = {"services": [
            service("TEST_BGC", "FeatureServer"),
            service("TEST_BGC", "MapServer"),
            service("OTHER_BUC", "MapServer"),
            service("TEST_BFC", "FeatureServer"),
            service("NOTBGC", "FeatureServer"),
        ]}
        selected = pipeline.select_services(catalogue, [])
        self.assertEqual(
            [(s["name"], s["type"]) for s in selected],
            [("OTHER_BUC", "MapServer"), ("TEST_BGC", "FeatureServer")],
        )
        with self.assertRaisesRegex(pipeline.PipelineError, "not found"):
            pipeline.select_services(catalogue, ["MISSING_BGC"])

    def test_discovers_nonzero_layer_id_and_skips_unqueryable_layers(self):
        client = ScriptedClient([
            {"layers": [{"id": 4}, {"id": 7}, {"id": 12}]},
            layer_metadata(id=4, geometryType="esriGeometryPoint"),
            layer_metadata(),
            layer_metadata(id=12, capabilities="Map"),
        ])
        with self.assertLogs(pipeline.LOG, level="WARNING"):
            layers = pipeline.discover_layers(client, {"url": "https://example.test/service"})
        self.assertEqual([url for url, _ in layers], ["https://example.test/service/7"])

    def test_area_fields_require_a_single_matching_pair(self):
        self.assertEqual(pipeline.area_fields(layer_metadata()), ("TESTCD", "TESTNM"))
        for extra in ("OTHERCD", "OTHERNM"):
            metadata = layer_metadata()
            metadata["fields"].append({"name": extra})
            with self.assertRaisesRegex(pipeline.PipelineError, "Ambiguous"):
                pipeline.area_fields(metadata)
        metadata = layer_metadata()
        metadata["fields"][2]["name"] = "OTHERNM"
        with self.assertRaisesRegex(pipeline.PipelineError, "do not match"):
            pipeline.area_fields(metadata)

    def test_display_field_disambiguates_joined_classification_or_lookup_fields(self):
        metadata = layer_metadata(displayField="TESTNM")
        metadata["fields"].extend([{"name": "OTHERCD"}, {"name": "OTHERNM"}])
        self.assertEqual(pipeline.area_fields(metadata), ("TESTCD", "TESTNM"))
        metadata["displayField"] = "FID"
        with self.assertRaisesRegex(pipeline.PipelineError, "Ambiguous"):
            pipeline.area_fields(metadata)

    def test_explicit_fields_resolve_case_insensitively_and_reject_schema_changes(self):
        metadata = layer_metadata()
        self.assertEqual(
            pipeline.area_fields(metadata, override=("testcd", "testnm")),
            ("TESTCD", "TESTNM"),
        )
        for override in (("MISSINGCD", None), ("TESTCD", "MISSINGNM")):
            with self.subTest(override=override):
                with self.assertRaisesRegex(pipeline.PipelineError, "missing or ambiguous"):
                    pipeline.area_fields(metadata, override=override)
        metadata["fields"].append({"name": "testcd"})
        with self.assertRaisesRegex(pipeline.PipelineError, "missing or ambiguous"):
            pipeline.area_fields(metadata, override=("TESTCD", None))


class PublicationTests(unittest.TestCase):
    def test_service_overrides_publish_truncated_fields_and_code_only_schemas(self):
        cases = [
            ("CB_JUN_1921_EW_BGC_V2", "CTYCB1921C", "CTYCB1921N"),
            ("Covid_Infection_Survey_Dec_2020_UK_BGC", "CIS20CD", None),
            ("Covid_Infection_Survey_Dec_2020_UK_BUC", "CIS20CD", None),
            ("OA_2021_EW_BGC_V2_RUC", "oa21cd", None),
            ("OA_DEC_2011_BGC_EW_joined", "OA11CD", None),
            ("OA_EW_BGC_with_RUCOA11", "oa11cd", None),
        ]
        for service_name, code, name in cases:
            with self.subTest(service=service_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                metadata = layer_metadata(id=0, displayField=code)
                metadata["fields"] = [
                    {"name": "FID", "type": "esriFieldTypeOID"},
                    {"name": code, "type": "esriFieldTypeString"},
                ]
                if name is not None:
                    metadata["fields"].append({"name": name, "type": "esriFieldTypeString"})
                elif service_name.startswith("OA"):
                    metadata["fields"].extend([
                        {"name": field, "type": "esriFieldTypeString"}
                        for field in ("LSOA21CD", "LSOA21NM", "RUC21CD", "RUC21NM")
                    ])
                data = page([1, 2])
                data["fields"] = metadata["fields"]
                for feature in data["features"]:
                    attributes = feature["attributes"]
                    code_value = attributes.pop("TESTCD")
                    name_value = attributes.pop("TESTNM")
                    attributes[code] = code_value
                    if name is not None:
                        attributes[name] = name_value
                    elif service_name.startswith("OA"):
                        attributes.update(
                            LSOA21CD="E01000001", LSOA21NM="Parent area",
                            RUC21CD="1", RUC21NM="Urban classification",
                        )
                client = ScriptedClient([
                    {"layers": [{"id": 0}]}, metadata,
                    {"count": 2}, data, {"count": 2},
                ])
                service = {"name": service_name, "url": "https://example.test/service"}
                with self.assertLogs(pipeline.LOG, level="WARNING") as logs:
                    status = pipeline.refresh(
                        client, [service], root / "outputs", root / "manifest.json",
                    )
                self.assertEqual(status, 0, "\n".join(logs.output))
                topology = json.loads((root / "outputs" / f"{service_name}.json").read_text())
                self.assertEqual(
                    [g["properties"] for g in topology["objects"]["data"]["geometries"]],
                    [
                        {"AREACD": f"E{oid:08}", "AREANM": f"Area {oid}" if name else f"E{oid:08}"}
                        for oid in (1, 2)
                    ],
                )
                queries = [params for _, params in client.calls if "outFields" in params]
                self.assertEqual(queries[0]["outFields"], f"FID,{code}" + (f",{name}" if name else ""))
                manifest = json.loads((root / "manifest.json").read_text())
                record = manifest["datasets"][f"{service_name}.json"]
                self.assertEqual(record["field_override"]["name_from_code"], name is None)
                self.assertEqual(record["expected_count"], record["actual_count"])

    def test_field_overrides_do_not_apply_to_other_services_or_layers(self):
        for service_name, layer_id in (
            ("UNKNOWN_BGC", 0),
            ("Covid_Infection_Survey_Dec_2020_UK_BGC", 7),
        ):
            with self.subTest(service=service_name, layer=layer_id), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "outputs"
                output.mkdir()
                old = output / f"{service_name}.json"
                old.write_text("previous topology\n")
                metadata = layer_metadata(id=layer_id)
                metadata["fields"] = [
                    {"name": "FID", "type": "esriFieldTypeOID"}, {"name": "CIS20CD"},
                ]
                client = ScriptedClient([{"layers": [{"id": layer_id}]}, metadata])
                service = {"name": service_name, "url": "https://example.test/service"}
                with self.assertLogs(pipeline.LOG, level="ERROR"):
                    status = pipeline.refresh(client, [service], output, root / "manifest.json")
                self.assertEqual(status, 1)
                self.assertEqual(old.read_text(), "previous topology\n")
                manifest = json.loads((root / "manifest.json").read_text())
                self.assertIsNone(manifest["datasets"][old.name]["field_override"])

    def test_complete_topology_retains_only_area_properties_and_shared_boundaries(self):
        client = ScriptedClient([{"count": 2}, page([1, 2]), {"count": 2}])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "boundaries.json"
            self.assertEqual(
                pipeline.publish(client, "https://example.test/7", layer_metadata(), output),
                (2, 2),
            )
            topology = json.loads(output.read_text())
        geometries = topology["objects"]["data"]["geometries"]
        self.assertEqual(len(geometries), 2)
        self.assertEqual(
            [g["properties"] for g in geometries],
            [
                {"AREACD": "E00000001", "AREANM": "Area 1"},
                {"AREACD": "E00000002", "AREANM": "Area 2"},
            ],
        )
        arc_ids = [{arc if arc >= 0 else ~arc for ring in g["arcs"] for arc in ring} for g in geometries]
        self.assertTrue(arc_ids[0].intersection(arc_ids[1]))

    def test_failed_validation_preserves_existing_output(self):
        for label in ("incomplete", "duplicate_code", "null_geometry", "invalid_geometry"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                data = page([1, 2])
                if label == "duplicate_code":
                    data["features"][1]["attributes"]["TESTCD"] = "E00000001"
                elif label == "null_geometry":
                    data["features"][0]["geometry"] = None
                elif label == "invalid_geometry":
                    data["features"][0]["geometry"]["rings"] = [
                        [[1, 0], [2, 1], [1, 1], [2, 0], [1, 0]]
                    ]
                responses = [{"count": 3}, data] if label == "incomplete" else [
                    {"count": 2}, data, {"count": 2},
                ]
                output = Path(temporary) / "boundaries.json"
                output.write_text("existing output\n")
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.publish(
                        ScriptedClient(responses), "https://example.test/7", layer_metadata(), output,
                    )
                self.assertEqual(output.read_text(), "existing output\n")
                self.assertEqual(list(output.parent.iterdir()), [output])

    def test_opt_in_repair_publishes_self_intersections_without_losing_features(self):
        data = page([1, 2])
        data["features"][0]["geometry"]["rings"] = [
            [[1, 0], [2, 1], [1, 1], [2, 0], [1, 0]]
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = ScriptedClient([
                {"layers": [{"id": 7}]}, layer_metadata(),
                {"count": 2}, data, {"count": 2},
            ])
            service = {"name": "TEST_BGC", "url": "https://example.test/service"}
            with self.assertLogs(pipeline.LOG, level="WARNING") as logs:
                status = pipeline.refresh(
                    client, [service], root / "outputs", root / "manifest.json",
                    repair_invalid_geometry=True,
                )
            self.assertEqual(status, 0)
            self.assertIn("Repaired 1 invalid geometries", logs.output[0])
            self.assertIn("E00000001", logs.output[0])
            topology = json.loads((root / "outputs" / "TEST_BGC.json").read_text())
            geometries = topology["objects"]["data"]["geometries"]
            self.assertEqual(len(geometries), 2)
            self.assertTrue(all(g["type"] in ("Polygon", "MultiPolygon") for g in geometries))
            self.assertEqual(
                [g["properties"]["AREACD"] for g in geometries],
                ["E00000001", "E00000002"],
            )
            manifest = json.loads((root / "manifest.json").read_text())
            record = manifest["datasets"]["TEST_BGC.json"]
            self.assertEqual(record["status"], "success")
            self.assertEqual(record["expected_count"], record["actual_count"])
            self.assertTrue(record["repair_invalid_geometry"])

    def test_repair_rejects_collapsed_or_missing_geometry_and_preserves_output(self):
        for label in ("collapsed", "null"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                data = page([1])
                data["features"][0]["geometry"] = None if label == "null" else {
                    "rings": [[[1, 0], [2, 0], [3, 0], [1, 0]]]
                }
                output = Path(temporary) / "boundaries.json"
                output.write_text("existing output\n")
                client = ScriptedClient([{"count": 1}, data, {"count": 1}])
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.publish(
                        client, "https://example.test/7", layer_metadata(), output,
                        repair_invalid_geometry=True,
                    )
                self.assertEqual(output.read_text(), "existing output\n")

    def test_atomic_write_failure_preserves_existing_output_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "boundaries.json"
            output.write_text("existing output\n")
            with patch.object(pipeline.os, "replace", side_effect=OSError("disk error")):
                with self.assertRaisesRegex(OSError, "disk error"):
                    pipeline.atomic_json(output, {"type": "Topology"})
            self.assertEqual(output.read_text(), "existing output\n")
            self.assertEqual(list(output.parent.iterdir()), [output])

    def test_failed_refresh_records_error_retains_last_success_and_continues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "outputs"
            output.mkdir()
            old = output / "A_BGC.json"
            old.write_text("previous topology\n")
            manifest_path = root / "manifest.json"
            pipeline.atomic_json(manifest_path, {"datasets": {
                old.name: {"output": str(old), "last_success": "previous timestamp"},
            }})
            services = [
                {"name": name, "url": f"https://example.test/{name}", "type": "FeatureServer"}
                for name in ("A_BGC", "B_BGC")
            ]
            client = ScriptedClient([
                {"layers": [{"id": 7}]}, layer_metadata(),
                {"count": 3}, page([1, 2]),
                {"layers": [{"id": 7}]}, layer_metadata(),
                {"count": 2}, page([1, 2]), {"count": 2},
            ])
            with self.assertLogs(pipeline.LOG, level="ERROR"):
                status = pipeline.refresh(client, services, output, manifest_path)
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(status, 1)
            self.assertEqual(old.read_text(), "previous topology\n")
            self.assertEqual(manifest["datasets"]["A_BGC.json"]["status"], "failed")
            self.assertIn("expected 3", manifest["datasets"]["A_BGC.json"]["error"])
            self.assertEqual(manifest["datasets"]["A_BGC.json"]["last_success"], "previous timestamp")
            successful = manifest["datasets"]["B_BGC.json"]
            self.assertEqual(successful["status"], "success")
            self.assertEqual(successful["expected_count"], successful["actual_count"])
            self.assertEqual(manifest["failed_datasets_or_services"], 1)
            self.assertIsNotNone(manifest["finished_at"])

    def test_multiple_layers_use_distinct_filenames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = ScriptedClient([
                {"layers": [{"id": 7}, {"id": 9}]},
                layer_metadata(), layer_metadata(id=9),
                {"count": 1}, page([1]), {"count": 1},
                {"count": 1}, page([2]), {"count": 1},
            ])
            service = {"name": "TEST_BGC", "url": "https://example.test/service"}
            self.assertEqual(
                pipeline.refresh(client, [service], root / "outputs", root / "manifest.json"), 0,
            )
            self.assertEqual(
                sorted(path.name for path in (root / "outputs").iterdir()),
                ["TEST_BGC__layer-7.json", "TEST_BGC__layer-9.json"],
            )

    def test_main_returns_refresh_failure_status_and_closes_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalogue = Path(temporary) / "catalogue.json"
            catalogue.write_text(json.dumps({"services": [{
                "name": "TEST_BGC", "type": "FeatureServer", "url": "https://example.test",
            }]}))
            with patch.object(pipeline, "ArcGISClient") as client, patch.object(
                pipeline, "refresh", return_value=1
            ):
                self.assertEqual(pipeline.main([str(catalogue)]), 1)
                client.return_value.close.assert_called_once()

    def test_main_passes_explicit_geometry_repair_option_to_refresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalogue = Path(temporary) / "catalogue.json"
            catalogue.write_text(json.dumps({"services": [{
                "name": "TEST_BGC", "type": "FeatureServer", "url": "https://example.test",
            }]}))
            with patch.object(pipeline, "ArcGISClient"), patch.object(
                pipeline, "refresh", return_value=0
            ) as refresh:
                self.assertEqual(
                    pipeline.main([str(catalogue), "--repair-invalid-geometry"]), 0,
                )
                self.assertTrue(refresh.call_args.kwargs["repair_invalid_geometry"])


if __name__ == "__main__":
    unittest.main()
