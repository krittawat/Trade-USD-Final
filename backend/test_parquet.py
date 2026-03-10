import duckdb

conn = duckdb.connect(':memory:')
df = conn.execute("SELECT MIN(time) as start, MAX(time) as end, COUNT(*) as bars FROM read_parquet('backend/data/exports/mtf/XAUUSDc_M5.parquet')").df()
print("XAUUSDc_M5:", df)

df = conn.execute("SELECT MIN(time) as start, MAX(time) as end, COUNT(*) as bars FROM read_parquet('backend/data/exports/mtf/XAGUSDc_M5.parquet')").df()
print("XAGUSDc_M5:", df)
