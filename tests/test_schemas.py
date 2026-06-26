"""Tests for JSON Schema files under src/dstrack/schemas/."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


def test_snapshot_schemas():
    """Verify every JSON Schema file in the schemas folder is a valid Draft 2020-12 schema.

    Collects all .json files, runs Draft202012Validator.check_schema on each, and
    fails with the list of offending files if any schema is malformed.
    """
    here = Path(__file__).parent
    schema_folder = here.parent / "src" / "dstrack" / "schemas"
    schema_files = list(schema_folder.glob("*.json"))
    if not schema_files:
        pytest.fail(f"Could not find any schema files in {schema_folder}")

    invalid_schemas = []
    for f in schema_files:
        with open(f) as handle:
            schema = json.load(handle)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            invalid_schemas.append(f)

    if invalid_schemas:
        pytest.fail(f"The following schemas failed checks: {invalid_schemas}")
