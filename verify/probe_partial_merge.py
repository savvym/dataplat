"""Probe: does lancedb 0.30.2 merge_insert with partial columns preserve missing columns?"""
import lancedb, pyarrow as pa, os, sys

storage_options = {
    "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
    "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
    "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
    "aws_region":            "us-east-1",
    "allow_http":            "true",
}
db = lancedb.connect("s3://lance/probe_col_test", storage_options=storage_options)

schema = pa.schema([
    ("id",   pa.string()),
    ("colA", pa.float32()),
    ("colB", pa.string()),
])
initial = pa.table({
    "id":   ["r1", "r2"],
    "colA": pa.array([1.0, 2.0], pa.float32()),
    "colB": ["original_B1", "original_B2"],
})
tbl = db.create_table("probe_col_test", data=initial, schema=schema)

# merge_insert with only id + colA (no colB)
partial = pa.table({
    "id":   ["r1"],
    "colA": pa.array([99.0], pa.float32()),
})
tbl.merge_insert("id").when_matched_update_all().execute(partial)

rows = {r["id"]: r for r in tbl.search().select(["id", "colA", "colB"]).to_list()}
preserved = rows["r1"]["colB"] == "original_B1"
unchanged = rows["r2"]["colA"] == 2.0
updated = rows["r1"]["colA"] == 99.0
print(f"partial merge preserves colB: {preserved}")
print(f"colA was updated to 99.0:     {updated}")
print(f"unaffected row r2 unchanged:  {unchanged}")

db.drop_table("probe_col_test")
if preserved and updated:
    print("\n==> D3a VIABLE: partial-column merge_insert preserves absent columns")
    sys.exit(0)
else:
    print("\n==> D3a NOT VIABLE: D3b (read-modify-write) is required")
    sys.exit(1)
