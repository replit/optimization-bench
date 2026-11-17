PRAGMA page_size=4096;
PRAGMA auto_vacuum=NONE;
PRAGMA journal_mode=DELETE;
PRAGMA synchronous=FULL;
CREATE TABLE headers(source_tag INTEGER PRIMARY KEY,fields_json TEXT NOT NULL);
CREATE TABLE mirror(
 record_id INTEGER PRIMARY KEY,
 source_tag INTEGER NOT NULL,
 source_ordinal INTEGER NOT NULL,
 record_json TEXT NOT NULL,
 retain INTEGER NOT NULL CHECK(retain IN(0,1)),
 local_note TEXT NOT NULL DEFAULT 'local-only',
 UNIQUE(source_tag,source_ordinal)
);
CREATE INDEX mirror_retention ON mirror(retain,source_tag);
PRAGMA user_version=2;
