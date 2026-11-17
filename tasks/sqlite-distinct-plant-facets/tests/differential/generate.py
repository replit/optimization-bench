"""Seeded SQL over the task's public surface: DISTINCT/GROUP BY facets, FTS5,
UPSERT and transactions, indexes/foreign keys/VACUUM/integrity, R*Tree, and
percentile/window/decimal/JSON functions, including error paths.

Every multi-row result has a total ORDER BY, and no case depends on scan order
(no order-dependent float sums, RETURNING over several rows or row-order
constraint checks), so a mismatch is a behavior difference, not a plan choice.
"""

COUNTRIES = ["AR", "BR", "CL", "DE", "FR", "IN", "JP", "NZ", "US", "ZA"]
# One spelling per family: DISTINCT and GROUP BY over a NOCASE column keep an
# arbitrary spelling of equal values, so mixed spellings would test plan order.
FAMILIES = ["Rosaceae", "Fagaceae", "Pinaceae", "Asteraceae", "Poaceae",
            "Orchidaceae", "Fabaceae", "Lamiaceae", "Cactaceae", "Betulaceae"]
GENERA = ["acer", "quercus", "pinus", "betula", "salix", "rosa", "o'neil", "x\\ny", "café", ""]
HABITS = ["tree", "shrub", "herb", "vine", "succulent", "Tree"]
WORDS = ["alpha", "beta", "gamma", "delta", "forest", "river", "stone", "light", "café",
         "naïve", "ÉCOLE", "λόγος", "雪", "running", "runs", "ran", "resume", "résumé"]
SITES = ["north", "south", "east", "west", "Δ-site", "o'hare"]
FAMILY_ORDER = ("facets", "fts5", "upsert", "integrity", "rtree", "analytics")


def text(value):
    return "'" + value.replace("'", "''") + "'"


def maybe_null(rng, literal, rate=.15):
    return "NULL" if rng.random() < rate else literal


def integer(rng):
    return str(rng.choice([0, 1, -1, 7, 42, 2 ** 31, -2 ** 31, 2 ** 63 - 1,
                           rng.randrange(-1000, 1000), rng.randrange(-1000, 1000)]))


def real(rng):
    return repr(rng.choice([0.0, -0.0, 0.5, 1e-300, 1.5e300,
                            round(rng.uniform(-1e4, 1e4), rng.randrange(0, 7))]))


def any_value(rng):
    kind = rng.randrange(6)
    if kind == 0:
        return integer(rng)
    if kind == 1:
        return real(rng)
    if kind == 2:
        return text(rng.choice(WORDS + GENERA))
    if kind == 3:
        return "X'" + bytes(rng.randrange(256) for _ in range(rng.randrange(9))).hex() + "'"
    if kind == 4:
        return "'a'||char(0)||'b'"
    return "NULL"


def inserts(table, rows, chunk=50):
    return [f"INSERT INTO {table} VALUES " + ",".join(rows[i:i + chunk])
            for i in range(0, len(rows), chunk)]


def facets(rng):
    rows = []
    for i in range(rng.choice([0, 1, 6, 40, 200])):
        rows.append(f"({i}, {text(rng.choice(COUNTRIES))}, "
                    f"{maybe_null(rng, text(rng.choice(FAMILIES)))}, "
                    f"{maybe_null(rng, text(rng.choice(GENERA)))}, {text(rng.choice(HABITS))}, "
                    f"{rng.randrange(2)}, {rng.randrange(1, 4000)}, {maybe_null(rng, real(rng))})")
    country = text(rng.choice(COUNTRIES))
    family = text(rng.choice([str.upper, str.lower, str])(rng.choice(FAMILIES)))
    statements = ["CREATE TABLE plants(id INTEGER PRIMARY KEY, country TEXT, family TEXT COLLATE NOCASE, "
                  "genus TEXT, habit TEXT, native INTEGER, height INTEGER, spread REAL)"]
    if rng.random() < .5:
        statements.append("CREATE INDEX plants_country_family ON plants(country, family)")
    if rng.random() < .3:
        statements.append("CREATE INDEX plants_genus ON plants(genus, habit)")
    statements += inserts("plants", rows)
    if rng.random() < .3:
        statements.append("ANALYZE")
    statements += [
        f"SELECT DISTINCT family FROM plants WHERE country = {country} ORDER BY family",
        "SELECT DISTINCT country, family FROM plants ORDER BY 1, 2",
        "SELECT DISTINCT genus, habit FROM plants WHERE native ORDER BY 1, 2",
        "SELECT family, count(*), count(DISTINCT genus), count(genus), sum(height), avg(height), "
        "min(spread), max(spread) FROM plants GROUP BY family ORDER BY family",
        "SELECT habit, native, count(*), group_concat(genus, '|' ORDER BY genus, id) "
        "FROM plants GROUP BY habit, native ORDER BY 1, 2",
        "SELECT country, count(*) FILTER (WHERE native), count(DISTINCT family COLLATE BINARY) "
        "FROM plants GROUP BY country HAVING count(*) > 1 ORDER BY 1",
        "SELECT DISTINCT lower(genus), length(genus), hex(genus) FROM plants "
        "WHERE family LIKE 'r%' OR genus GLOB '*a*' ORDER BY 1, 2, 3",
        f"SELECT id, family, genus FROM plants WHERE family = {family} ORDER BY id",
        "SELECT * FROM plants WHERE id IN (SELECT max(id) FROM plants GROUP BY country) ORDER BY id",
        "SELECT count(*) FROM (SELECT DISTINCT country, family, genus FROM plants)",
        "SELECT p.country, count(DISTINCT q.family) FROM plants p JOIN plants q "
        "ON p.genus = q.genus AND p.id < q.id GROUP BY p.country ORDER BY 1",
        "SELECT DISTINCT family FROM plants WHERE habit IN ('tree', 'shrub') "
        "UNION SELECT DISTINCT genus FROM plants ORDER BY 1",
        "SELECT nosuch FROM plants",
        "SELECT DISTINCT FROM plants",
    ]
    return statements


def fts5(rng):
    option = rng.choice(["", ", tokenize='porter'", ", tokenize='unicode61 remove_diacritics 2'",
                         ", prefix='2 3'", ", tokenize='trigram'"])
    rows = []
    for i in range(rng.choice([0, 1, 10, 60])):
        title = " ".join(rng.choice(WORDS) for _ in range(rng.randrange(1, 4)))
        body = " ".join(rng.choice(WORDS) for _ in range(rng.randrange(3, 20)))
        rows.append(f"({i + 1}, {text(title)}, {text(body)})")
    statements = [f"CREATE VIRTUAL TABLE docs USING fts5(title, body{option})"]
    statements += ["INSERT INTO docs(rowid, title, body) VALUES " + ",".join(rows[i:i + 50])
                   for i in range(0, len(rows), 50)]
    a, b = rng.choice(WORDS), rng.choice(WORDS)
    queries = [a, a[:2] + "*", f"{a} {b}", f"{a} OR {b}", f"{a} NOT {b}", f'"{a} {b}"',
               f"NEAR({a} {b}, 3)", f"title:{a}", f"^{a}", "AND", '"unterminated']
    for query in rng.sample(queries, 6):
        statements.append(f"SELECT rowid, bm25(docs), highlight(docs, 1, '[', ']') FROM docs "
                          f"WHERE docs MATCH {text(query)} ORDER BY rank, rowid")
        statements.append(f"SELECT rowid, snippet(docs, 1, '<', '>', '...', 5) FROM docs "
                          f"WHERE docs MATCH {text(query)} ORDER BY rowid")
    statements += [
        f"UPDATE docs SET body = {text(' '.join(rng.choice(WORDS) for _ in range(6)))} WHERE rowid = 3",
        "DELETE FROM docs WHERE rowid % 3 = 0",
        f"SELECT count(*), sum(rowid) FROM docs WHERE docs MATCH {text(a)}",
        "INSERT INTO docs(docs) VALUES('optimize')",
        "INSERT INTO docs(docs) VALUES('integrity-check')",
        f"SELECT rowid, bm25(docs, 2.0, 1.0) FROM docs WHERE docs MATCH {text(a + ' OR ' + b)} "
        "ORDER BY rank, rowid",
        "SELECT rowid, title FROM docs ORDER BY rowid",
    ]
    return statements


def upsert(rng):
    keys = [text(rng.choice(WORDS + GENERA)) for _ in range(8)]
    statements = ["CREATE TABLE kv(k TEXT PRIMARY KEY, v INTEGER NOT NULL, tag TEXT UNIQUE, n INTEGER DEFAULT 0)"
                  + rng.choice(["", " WITHOUT ROWID"])]
    for _ in range(rng.randrange(10, 40)):
        k, v, tag = rng.choice(keys), rng.randrange(-50, 50), maybe_null(rng, text(f"t{rng.randrange(12)}"), .3)
        statements.append(rng.choice([
            f"INSERT INTO kv(k, v, tag) VALUES ({k}, {v}, {tag}) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v + v, n = n + 1 RETURNING k, v, tag, n",
            f"INSERT INTO kv(k, v, tag) VALUES ({k}, {v}, {tag})",
            f"INSERT OR IGNORE INTO kv(k, v, tag) VALUES ({k}, {v}, {tag})",
            f"INSERT OR REPLACE INTO kv(k, v, tag) VALUES ({k}, {v}, {tag})",
            f"INSERT INTO kv(k, v) VALUES ({k}, NULL)",
            f"UPDATE kv SET v = v * 2, n = n + 1 WHERE k > {k}",
            f"DELETE FROM kv WHERE k = {k} RETURNING k, v",
            rng.choice(["BEGIN", "SAVEPOINT a", "RELEASE a", "ROLLBACK TO a", "COMMIT", "ROLLBACK"]),
            "SELECT changes(), total_changes()",
            "SELECT k, v, tag, n FROM kv ORDER BY k",
        ]))
    statements += ["SELECT k, v, tag, n, typeof(v) FROM kv ORDER BY k",
                   "SELECT count(*), sum(v), sum(n) FROM kv"]
    return statements


def integrity(rng):
    statements = [rng.choice(["PRAGMA foreign_keys = ON", "PRAGMA foreign_keys = OFF"]),
                  rng.choice(["PRAGMA journal_mode = WAL", "PRAGMA journal_mode = DELETE",
                              "PRAGMA journal_mode = TRUNCATE"]),
                  "CREATE TABLE parent(id INTEGER PRIMARY KEY, name TEXT)",
                  "CREATE TABLE child(id INTEGER PRIMARY KEY, "
                  "parent_id INTEGER REFERENCES parent(id) ON DELETE CASCADE, score INTEGER, note BLOB)"]
    parents = rng.choice([0, 3, 20, 120])
    statements += inserts("parent", [f"({i}, {text(rng.choice(WORDS))})" for i in range(parents)])
    # Children reference existing parents so the table fills with foreign keys on;
    # the explicit orphan insert below covers the violation.
    children = [f"({i}, {rng.randrange(parents) if parents else 1}, {rng.randrange(100)}, {any_value(rng)})"
                for i in range(rng.choice([0, 5, 60, 300]))]
    statements += inserts("child", children, chunk=25)
    statements += rng.sample([
        "CREATE INDEX child_parent ON child(parent_id)",
        "CREATE INDEX child_high ON child(score) WHERE score > 50",
        "CREATE INDEX parent_name ON parent(lower(name), id)",
        "CREATE UNIQUE INDEX child_score ON child(score)",
        "CREATE UNIQUE INDEX parent_id_name ON parent(id, name)",
    ], 3)
    statements += [
        "DELETE FROM parent WHERE id % 4 = 0",
        "PRAGMA foreign_key_check",
        "PRAGMA integrity_check",
        "SELECT parent_id, count(*), sum(score), max(length(note)) FROM child GROUP BY parent_id ORDER BY 1",
        "SELECT name, count(child.id) FROM parent LEFT JOIN child ON child.parent_id = parent.id "
        "GROUP BY parent.id ORDER BY parent.id",
        rng.choice(["VACUUM", "REINDEX"]),
        "--reopen",
        "PRAGMA integrity_check",
        "PRAGMA quick_check",
        "SELECT id, parent_id, score, quote(note) FROM child ORDER BY id",
        "SELECT name, id FROM parent WHERE lower(name) > 'm' ORDER BY lower(name), id",
        "INSERT INTO child(id, parent_id, score) VALUES (100000, 999999, 1)",
        "DROP INDEX IF EXISTS child_parent",
        "PRAGMA integrity_check",
    ]
    return statements


def rtree(rng):
    statements = [rng.choice(["CREATE VIRTUAL TABLE boxes USING rtree(id, minx, maxx, miny, maxy, +label)",
                              "CREATE VIRTUAL TABLE boxes USING rtree_i32(id, minx, maxx, miny, maxy, +label)"])]
    rows = []
    for i in range(rng.choice([0, 1, 30, 250])):
        x, y = rng.uniform(-180, 180), rng.uniform(-90, 90)
        rows.append(f"({i + 1}, {x:.4f}, {x + rng.uniform(0, 5):.4f}, {y:.4f}, {y + rng.uniform(0, 5):.4f}, "
                    f"{text(rng.choice(WORDS))})")
    statements += inserts("boxes", rows)
    for _ in range(5):
        x, y, w = rng.uniform(-180, 180), rng.uniform(-90, 90), rng.uniform(1, 60)
        statements.append(f"SELECT id, minx, maxx, miny, maxy, label FROM boxes WHERE minx >= {x:.3f} "
                          f"AND maxx <= {x + w:.3f} AND miny >= {y:.3f} AND maxy <= {y + w:.3f} ORDER BY id")
        statements.append(f"SELECT count(*), sum(id) FROM boxes WHERE maxx >= {x:.3f} AND minx <= {x + w:.3f}")
    statements += [
        "INSERT INTO boxes VALUES (100000, 5, 1, 0, 1, 'bad')",
        "UPDATE boxes SET maxx = maxx + 1, label = 'moved' WHERE id = 2",
        "DELETE FROM boxes WHERE id % 5 = 0",
        "SELECT rtreecheck('boxes')",
        "SELECT id, minx, maxx, label FROM boxes WHERE id BETWEEN 1 AND 12 ORDER BY id",
        "SELECT count(*) FROM boxes",
    ]
    return statements


def analytics(rng):
    rows = [f"({text(rng.choice(SITES))}, {rng.randrange(2000, 2006)}, "
            f"{maybe_null(rng, real(rng) if rng.random() < .2 else f'{rng.uniform(-40, 60):.3f}')}, "
            f"{rng.randrange(-100, 1000)})"
            for _ in range(rng.choice([0, 1, 12, 150]))]
    n = rng.choice([1, 10, 500])
    statements = ["CREATE TABLE obs(site TEXT, year INTEGER, value REAL, qty INTEGER)"]
    statements += inserts("obs", rows)
    statements += [
        "SELECT site, year, count(*), median(value), percentile(value, 98), percentile_cont(value, 0.1), "
        "percentile_disc(value, 0.9), percentile(value, 2) FROM obs GROUP BY site, year ORDER BY 1, 2",
        "SELECT site, sum(qty), avg(qty), min(value), max(value), count(value) FROM obs GROUP BY site ORDER BY 1",
        "SELECT site, year, value, row_number() OVER w, rank() OVER w, dense_rank() OVER w, "
        "lag(value) OVER w, lead(qty, 2, -1) OVER w, sum(qty) OVER (PARTITION BY site ORDER BY year, rowid "
        "ROWS BETWEEN 1 PRECEDING AND CURRENT ROW), ntile(3) OVER w FROM obs "
        "WINDOW w AS (PARTITION BY site ORDER BY value, rowid) ORDER BY site, value, rowid",
        "SELECT site, decimal_sum(CAST(qty AS TEXT) || '.25'), decimal(sum(qty)) FROM obs GROUP BY site ORDER BY 1",
        f"SELECT decimal_add('1.10', '2.205'), decimal_mul('123456789.123', '-0.0001'), "
        f"decimal_cmp('1.0', '1'), decimal_exp(decimal('{rng.randrange(-999, 999)}.5'), 3)",
        f"WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < {n}) "
        "SELECT sum(x), count(*), max(x), total(x) FROM c",
        "SELECT json_group_array(value ORDER BY rowid), json_group_object(site, qty ORDER BY rowid) FROM obs",
        "SELECT key, value, type FROM json_each('{\"a\":[1,2.5,{\"b\":null}],\"c\":\"x\\u00e9\"}') ORDER BY key",
        "SELECT json_extract('{\"a\":[1,2,{\"b\":null}]}', '$.a[2].b', '$.a[#-1]'), json('[1, 2]'), "
        "json_valid('{bad')",
        "SELECT round(value, 2), abs(qty), printf('%.3f|%5d|%x|%e', value, qty, qty, value), "
        "CAST(value AS TEXT), quote(value) FROM obs ORDER BY rowid LIMIT 20",
        "SELECT upper(site), substr(site, -3, 2), instr(site, 'o'), replace(site, 'o', '0'), "
        "trim(' ' || site || ' '), unicode(site), length(CAST(site AS BLOB)) FROM obs ORDER BY rowid LIMIT 20",
        "SELECT 9223372036854775807 + 1, -9223372036854775808 - 1, 1 / 0, 7 % 0, 2.0 / 3, 1e308 * 10",
        "SELECT abs(-9223372036854775808)",
        "SELECT median(value) FROM obs WHERE 0",
        "SELECT percentile(value, 101) FROM obs",
    ]
    return statements


BUILDERS = {"facets": facets, "fts5": fts5, "upsert": upsert, "integrity": integrity,
            "rtree": rtree, "analytics": analytics}


def generate(rng, budget):
    for i in range(min(budget, 12)):
        family = FAMILY_ORDER[i % len(FAMILY_ORDER)]
        yield {"family": family, "statements": BUILDERS[family](rng)}


SETUP = ("CREATE", "INSERT", "PRAGMA", "ANALYZE")


def shrink(case):
    # The judge keeps the last candidate that still mismatches: decreasing
    # prefixes first, then the leading setup plus one later statement each.
    statements = case["statements"]
    for size in sorted({len(statements) * 3 // 4, len(statements) // 2,
                        len(statements) // 4, len(statements) // 8}, reverse=True):
        if 0 < size < len(statements):
            yield {**case, "statements": statements[:size]}
    setup = 0
    while setup < len(statements) and statements[setup].startswith(SETUP):
        setup += 1
    for statement in reversed(statements[setup:]):
        yield {**case, "statements": statements[:setup] + [statement]}
