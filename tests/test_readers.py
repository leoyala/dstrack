from pathlib import Path

import pytest

from dstrack.readers import ColumnInfo, CsvReader, TabularReader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_csv(tmp_path: Path, name: str, content: str) -> Path:
    """Write *content* to *name* inside *tmp_path* and return the file path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def flat_rows(reader: CsvReader, batch_size: int = 1000) -> list[list[object]]:
    """Flatten all batches from *reader* into a single list of rows."""
    return [row for batch in reader.iter_batches(batch_size) for row in batch]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_csv_reader_satisfies_protocol(tmp_path: Path) -> None:
    """CsvReader is recognised as a TabularReader by isinstance."""
    path = write_csv(tmp_path, "a.csv", "x\n1\n")
    assert isinstance(CsvReader(path), TabularReader)


def test_arbitrary_class_satisfies_protocol() -> None:
    """Any class with the right methods satisfies TabularReader structurally."""

    class _Stub:
        def columns(self) -> list[ColumnInfo]:
            return []

        def iter_batches(self, batch_size: int = 1000):  # type: ignore[override]
            return iter([])

    assert isinstance(_Stub(), TabularReader)


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------


def test_detects_mixed_dtypes(tmp_path: Path) -> None:
    """Each column resolves to its expected dtype when multiple types are present."""
    content = (
        "name,age,score,active,created_at\n"
        "alice,30,1.5,true,2024-01-01\n"
        "bob,25,2.3,false,2024-06-15\n"
    )
    reader = CsvReader(write_csv(tmp_path, "mixed.csv", content))
    by_name = {c.name: c for c in reader.columns()}

    assert by_name["name"].dtype == "string"
    assert by_name["age"].dtype == "int64"
    assert by_name["score"].dtype == "float64"
    assert by_name["active"].dtype == "bool"
    assert by_name["created_at"].dtype == "datetime64"


def test_detects_negative_integers(tmp_path: Path) -> None:
    """Columns containing negative integers are typed as int64."""
    reader = CsvReader(write_csv(tmp_path, "neg.csv", "v\n-1\n-42\n0\n"))
    assert reader.columns()[0].dtype == "int64"


def test_detects_scientific_notation_as_float(tmp_path: Path) -> None:
    """Values in scientific notation are classified as float64."""
    reader = CsvReader(write_csv(tmp_path, "sci.csv", "v\n1.5e3\n2.0e-1\n"))
    assert reader.columns()[0].dtype == "float64"


def test_column_order_preserved(tmp_path: Path) -> None:
    """columns() returns headers in the same left-to-right order as the CSV."""
    reader = CsvReader(write_csv(tmp_path, "order.csv", "z,a,m\n1,2,3\n"))
    assert [c.name for c in reader.columns()] == ["z", "a", "m"]


def test_columns_cached(tmp_path: Path) -> None:
    """Repeated calls to columns() return the exact same object (not recomputed)."""
    reader = CsvReader(write_csv(tmp_path, "cached.csv", "a\n1\n"))
    assert reader.columns() is reader.columns()


# ---------------------------------------------------------------------------
# Nullable detection
# ---------------------------------------------------------------------------


def test_nullable_column_detected(tmp_path: Path) -> None:
    """A column with any blank cell is marked nullable; one without blanks is not."""
    reader = CsvReader(write_csv(tmp_path, "null.csv", "x,y\n1,\n2,hello\n"))
    by_name = {c.name: c for c in reader.columns()}
    assert by_name["y"].nullable is True
    assert by_name["x"].nullable is False


def test_null_pattern_strings_mark_nullable(tmp_path: Path) -> None:
    """Columns containing common null sentinel strings are marked nullable."""
    content = "a,b,c\n1,null,foo\n2,NaN,bar\n3,NA,baz\n"
    reader = CsvReader(write_csv(tmp_path, "null_patterns.csv", content))
    by_name = {c.name: c for c in reader.columns()}
    assert by_name["a"].nullable is False
    assert by_name["b"].nullable is True
    assert by_name["c"].nullable is False


def test_null_patterns_excluded_from_dtype_inference(tmp_path: Path) -> None:
    """Null sentinel strings do not prevent a column from resolving to a numeric dtype."""
    content = "x,y\n1,1.1\nnull,NaN\n3,3.3\nNA,n/a\n"
    reader = CsvReader(write_csv(tmp_path, "dtype_null.csv", content))
    by_name = {c.name: c for c in reader.columns()}
    assert by_name["x"].dtype == "int64"
    assert by_name["y"].dtype == "float64"


# ---------------------------------------------------------------------------
# iter_batches
# ---------------------------------------------------------------------------


def test_batches_partition_rows(tmp_path: Path) -> None:
    """iter_batches splits rows into chunks of the requested size, remainder in the last."""
    rows_csv = "\n".join(f"{i}" for i in range(1, 6))
    reader = CsvReader(write_csv(tmp_path, "five.csv", f"x\n{rows_csv}\n"))
    batches = list(reader.iter_batches(batch_size=2))

    assert len(batches) == 3
    assert batches[0] == [[1], [2]]
    assert batches[1] == [[3], [4]]
    assert batches[2] == [[5]]


def test_coerces_types(tmp_path: Path) -> None:
    """Row values are cast to native Python types (int, float, bool), not left as strings."""
    reader = CsvReader(
        write_csv(tmp_path, "types.csv", "n,f,b\n1,3.14,true\n-2,0.0,false\n")
    )
    rows = flat_rows(reader)

    assert rows[0] == [1, 3.14, True]
    assert rows[1] == [-2, 0.0, False]


def test_null_values_become_none(tmp_path: Path) -> None:
    """Empty CSV cells are yielded as None in row data."""
    reader = CsvReader(write_csv(tmp_path, "nulls.csv", "x,y\n1,\n2,hello\n"))
    rows = flat_rows(reader)

    assert rows[0] == [1, None]
    assert rows[1] == [2, "hello"]


def test_null_pattern_strings_become_none(tmp_path: Path) -> None:
    """Common null sentinel strings are coerced to None across all dtypes."""
    content = "i,f,s\n1,1.1,a\nnull,NaN,NULL\nNA,n/a,N/A\nnone,None,Null\n"
    reader = CsvReader(write_csv(tmp_path, "null_coerce.csv", content))
    rows = flat_rows(reader)

    assert rows[0] == [1, 1.1, "a"]
    assert rows[1] == [None, None, None]
    assert rows[2] == [None, None, None]
    assert rows[3] == [None, None, None]


def test_single_batch_when_rows_fit(tmp_path: Path) -> None:
    """When all rows fit in one batch, iter_batches yields exactly one chunk."""
    reader = CsvReader(write_csv(tmp_path, "small.csv", "x\n1\n2\n3\n"))
    batches = list(reader.iter_batches(batch_size=100))
    assert len(batches) == 1
    assert len(batches[0]) == 3


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_file_returns_no_columns(tmp_path: Path) -> None:
    """A completely empty file produces no columns and no batches."""
    reader = CsvReader(write_csv(tmp_path, "empty.csv", ""))
    assert reader.columns() == []
    assert list(reader.iter_batches()) == []


def test_header_only_returns_columns_no_batches(tmp_path: Path) -> None:
    """A header-only CSV reports columns but yields no data batches."""
    reader = CsvReader(write_csv(tmp_path, "header.csv", "a,b,c\n"))
    assert len(reader.columns()) == 3
    assert list(reader.iter_batches()) == []


def test_custom_delimiter(tmp_path: Path) -> None:
    """Passing a custom delimiter correctly splits columns and coerces values."""
    reader = CsvReader(
        write_csv(tmp_path, "semi.csv", "a;b\n1;2\n3;4\n"),
        delimiter=";",
    )
    cols = reader.columns()
    assert [c.name for c in cols] == ["a", "b"]
    assert flat_rows(reader) == [[1, 2], [3, 4]]


def test_column_info_is_immutable() -> None:
    """ColumnInfo fields cannot be reassigned after construction."""
    col = ColumnInfo(name="x", dtype="int64")
    with pytest.raises((AttributeError, TypeError)):
        col.name = "y"  # type: ignore[misc]


def test_raise_when_file_is_modified(tmp_path: Path) -> None:
    """iter_batches raises RuntimeError if the file changes after columns() is called."""
    reader = CsvReader(
        write_csv(tmp_path, "semi.csv", "a;b\n1;2\n3;4\n"),
        delimiter=";",
    )
    _ = reader.columns()
    write_csv(tmp_path, "semi.csv", "a;b\n1;2\n3;4\n")
    with pytest.raises(RuntimeError):
        _ = list(reader.iter_batches())
