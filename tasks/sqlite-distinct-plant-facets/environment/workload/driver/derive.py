#!/usr/bin/env python3
"""Deterministic derived-input builder for the SQLite suite (protected task glue).

usage: derive.py RECIPES FILES [--raw DIR] [--jobs N] [--prune] [--emit]

RECIPES is either {"outputs": [...], "keep": [...]} or an inputs manifest whose
documents list "files".

Every manifest document may list "files"; a file with a "derive" recipe is rebuilt
from authenticated raw upstream bytes (looked up by SHA-256 under --raw, default
FILES) and must reproduce the committed size and SHA-256 exactly (fail closed).
--emit prints the computed commitments instead of failing (authoring only).
--prune deletes FILES entries that no document references (raw archives), so raw
upstream bodies never persist next to the derived inputs.

The engine is the CPython sqlite3 module of the digest-pinned python base image
(Debian libsqlite3, independent of the candidate tree). Builders are the
candidate kits' builders with paths and split choices made explicit parameters;
Python hash randomization is irrelevant (no set/dict iteration reaches output
order) but PYTHONHASHSEED=0 is still expected from callers.
"""
import argparse
import bz2
import csv
import datetime
import gzip
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

csv.field_size_limit(1 << 30)


def h1(text):
    return hashlib.sha256(text.encode()).digest()[0]


def newdb(path):
    c = sqlite3.connect(path)
    c.execute("pragma journal_mode=off")
    c.execute("pragma synchronous=off")
    c.execute("pragma page_size=4096")
    return c


def finish(c):
    c.commit()
    c.execute("analyze")
    c.commit()
    c.execute("vacuum")
    c.close()


def nz(v):
    return None if v is None or v == "" else v


def ni(v):
    v = nz(v)
    return None if v is None else int(float(v))


def nf(v):
    v = nz(v)
    return None if v is None else float(v)


def zcsv(zpath, member):
    z = zipfile.ZipFile(zpath)
    name = [n for n in z.namelist() if n.endswith(member)][0]
    return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline=""))


# ---------------- agent 1: storage / maintenance ----------------
GEONAME_TYPES = "ITTTRRTTTTTTTTIIITT"


def b_geonames_integrity(out, raw, params, work):
    """GeoNames country dumps -> index-rich gazetteer DB, then seeded page damage."""
    texts = []
    for path, entry in raw:
        member = entry["name"].removesuffix(".zip") + ".txt"
        target = work / member
        with zipfile.ZipFile(path) as z, z.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        texts.append(target)
    clean = work / "clean.db"
    c = sqlite3.connect(clean)
    c.executescript("""
PRAGMA page_size=4096;
CREATE TABLE geoname(geonameid INTEGER PRIMARY KEY, name TEXT, asciiname TEXT, alternatenames TEXT,
  latitude REAL, longitude REAL, feature_class TEXT, feature_code TEXT, country_code TEXT, cc2 TEXT,
  admin1_code TEXT, admin2_code TEXT, admin3_code TEXT, admin4_code TEXT, population INTEGER,
  elevation INTEGER, dem INTEGER, timezone TEXT, modification_date TEXT);
""")

    def conv(i, v):
        if v == "":
            return None
        if GEONAME_TYPES[i] == "I":
            try:
                return int(v)
            except ValueError:
                return v
        if GEONAME_TYPES[i] == "R":
            try:
                return float(v)
            except ValueError:
                return v
        return v
    with c:
        for s in texts:
            rows = []
            for line in open(s, encoding="utf-8", newline="\n"):
                f = line.rstrip("\n").split("\t")
                if len(f) != 19:
                    raise ValueError("GeoNames row shape")
                rows.append([conv(i, v) for i, v in enumerate(f)])
            c.executemany("INSERT INTO geoname VALUES(" + ",".join("?" * 19) + ")", rows)
    c.executescript("""
CREATE INDEX geoname_name ON geoname(name);
CREATE INDEX geoname_asciiname ON geoname(asciiname);
CREATE INDEX geoname_feature ON geoname(feature_class, feature_code);
CREATE INDEX geoname_admin ON geoname(country_code, admin1_code, admin2_code);
CREATE INDEX geoname_population ON geoname(population);
CREATE INDEX geoname_timezone_moddate ON geoname(timezone, modification_date);
""")
    c.close()
    corrupt(clean, out, params["seed"], params["n"])


def schema_pages(path):
    """Pages of the sqlite_schema b-tree (root page 1), walked without dbstat."""
    data = Path(path).read_bytes()
    psz = struct.unpack(">H", data[16:18])[0] or 65536
    seen, stack = set(), [1]
    while stack:
        pg = stack.pop()
        if pg in seen:
            continue
        seen.add(pg)
        off = (pg - 1) * psz
        hdr = off + (100 if pg == 1 else 0)
        kind = data[hdr]
        if kind == 0x05:  # interior table page
            ncell = struct.unpack(">H", data[hdr + 3:hdr + 5])[0]
            stack.append(struct.unpack(">I", data[hdr + 8:hdr + 12])[0])
            for i in range(ncell):
                cp = struct.unpack(">H", data[hdr + 12 + 2 * i:hdr + 14 + 2 * i])[0]
                stack.append(struct.unpack(">I", data[off + cp:off + cp + 4])[0])
    return seen


def corrupt(src, out, seed, n):
    """Kit corrupt_db.py: one damaged b-tree page per equal stripe; schema pages untouched."""
    shutil.copyfile(src, out)
    c = sqlite3.connect("file:%s?immutable=1" % src, uri=True)
    psz = c.execute("PRAGMA page_size").fetchone()[0]
    npg = c.execute("PRAGMA page_count").fetchone()[0]
    c.close()
    protected = schema_pages(src) | {1}
    data = bytearray(Path(out).read_bytes())
    usable = psz - data[20]
    rng = random.Random(seed)

    def varint(b, i):
        v = 0
        for k in range(9):
            x = b[i + k]
            if k == 8:
                return (v << 8) | x, i + 9
            v = (v << 7) | (x & 0x7f)
            if x < 0x80:
                return v, i + k + 1
    stripes = [(2 + i * (npg - 2) // n, 2 + (i + 1) * (npg - 2) // n) for i in range(n)]
    edits = 0
    for si, (lo, hi) in enumerate(stripes):
        want_index = si % 2 == 1
        for _ in range(5000):
            pg = rng.randrange(lo, max(lo + 1, hi))
            if pg in protected:
                continue
            off = (pg - 1) * psz
            t = data[off]
            if want_index and t == 0x0A:
                ncell = struct.unpack(">H", data[off + 3:off + 5])[0]
                if ncell < 2:
                    continue
                cell = rng.randrange(ncell)
                cptr = struct.unpack(">H", data[off + 8 + 2 * cell:off + 10 + 2 * cell])[0]
                nload, body = varint(data, off + cptr)
                maxlocal = (usable - 12) * 64 // 255 - 23
                if nload > maxlocal or nload < 3:
                    continue
                data[body + nload - 1] ^= 0x01
                edits += 1
                break
            if not want_index and t in (0x05, 0x0D):
                old = data[off + 7]
                data[off + 7] = (old + 1 + rng.randrange(40)) % 256
                edits += 1
                break
        else:
            for _ in range(5000):
                pg = rng.randrange(lo, max(lo + 1, hi))
                off = (pg - 1) * psz
                if pg not in protected and data[off] in (0x05, 0x0D):
                    old = data[off + 7]
                    data[off + 7] = (old + 1 + rng.randrange(40)) % 256
                    edits += 1
                    break
    Path(out).write_bytes(data)


def b_pageviews(out, raw, params, work):
    (src, _), = raw
    c = sqlite3.connect(out)
    c.executescript("PRAGMA page_size=4096; CREATE TABLE pageviews(id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
                    "title TEXT NOT NULL, views INTEGER NOT NULL, bytes INTEGER NOT NULL);")

    def rows():
        with gzip.open(src, "rt", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
            for line in f:
                p = line.rstrip("\n").split(" ")
                if len(p) != 4:
                    continue
                yield (p[0], p[1].encode("utf-8", "surrogateescape").decode("utf-8", "replace"),
                       int(p[2]), int(p[3]))
    with c:
        c.executemany("INSERT INTO pageviews(project, title, views, bytes) VALUES(?,?,?,?)", rows())
    c.executescript("""
CREATE UNIQUE INDEX pageviews_project_title ON pageviews(project, title);
CREATE INDEX pageviews_title ON pageviews(title);
CREATE INDEX pageviews_views ON pageviews(views);
CREATE INDEX pageviews_project_views ON pageviews(project, views);
""")
    c.close()


def b_zip_member(out, raw, params, work):
    (src, _), = raw
    with zipfile.ZipFile(src) as z, z.open(params["member"]) as f, open(out, "wb") as g:
        shutil.copyfileobj(f, g)


def b_bunzip2(out, raw, params, work):
    (src, _), = raw
    with bz2.open(src, "rb") as f, open(out, "wb") as g:
        shutil.copyfileobj(f, g)


# ---------------- agent 2: query / function paths ----------------
def b_usgs(out, raw, params, work):
    c = newdb(out)
    c.execute("create table events(id text primary key, doc text not null)")
    for path, _ in raw:
        fc = json.load(open(path))
        for feat in fc["features"]:
            if h1("usgs-v1:" + feat["properties"]["net"]) % 2 != params["parity"]:
                continue
            c.execute("insert into events values(?,?)",
                      (feat["id"], json.dumps(feat, separators=(",", ":"), ensure_ascii=False)))
    finish(c)


STORM_COLS = ["EVENT_ID", "EPISODE_ID", "STATE", "YEAR", "MONTH_NAME", "EVENT_TYPE", "CZ_TYPE", "CZ_NAME", "WFO",
              "BEGIN_DATE_TIME", "CZ_TIMEZONE", "INJURIES_DIRECT", "INJURIES_INDIRECT", "DEATHS_DIRECT",
              "DEATHS_INDIRECT", "DAMAGE_PROPERTY", "DAMAGE_CROPS", "SOURCE", "MAGNITUDE", "MAGNITUDE_TYPE",
              "BEGIN_LOCATION", "BEGIN_LAT", "BEGIN_LON", "EPISODE_NARRATIVE", "EVENT_NARRATIVE"]


def b_storms(out, raw, params, work):
    c = newdb(out)
    c.execute("create table storms(event_id integer primary key, episode_id integer, state text, year integer, "
              "month_name text, event_type text, cz_type text, cz_name text, wfo text, begin_date_time text, "
              "cz_timezone text, injuries_direct integer, injuries_indirect integer, deaths_direct integer, "
              "deaths_indirect integer, damage_property text, damage_crops text, source text, magnitude real, "
              "magnitude_type text, begin_location text, begin_lat real, begin_lon real, episode_narrative text, "
              "event_narrative text)")
    c.execute("create index idx_storms_state_year on storms(state, year)")
    num_i = {"EVENT_ID", "EPISODE_ID", "YEAR", "INJURIES_DIRECT", "INJURIES_INDIRECT", "DEATHS_DIRECT",
             "DEATHS_INDIRECT"}
    num_f = {"MAGNITUDE", "BEGIN_LAT", "BEGIN_LON"}
    for path, _ in raw:
        rows = []
        for r in csv.DictReader(io.TextIOWrapper(gzip.open(path), encoding="utf-8", errors="replace", newline="")):
            if h1("storms-v1:" + r["STATE"]) % 2 != params["parity"]:
                continue
            rows.append(tuple((ni(r[k]) if k in num_i else nf(r[k]) if k in num_f else nz(r[k]))
                              for k in STORM_COLS))
        c.executemany("insert into storms values(" + ",".join("?" * len(STORM_COLS)) + ")", rows)
    finish(c)


GEO = ["geonameid", "name", "asciiname", "alternatenames", "latitude", "longitude", "feature_class", "feature_code",
       "country_code", "cc2", "admin1_code", "admin2_code", "admin3_code", "admin4_code", "population", "elevation",
       "dem", "timezone", "modification_date"]


def b_geonames_rtree(out, raw, params, work):
    (path, _), = raw
    c = newdb(out)
    c.execute("create table places(geonameid integer primary key, name text, asciiname text, latitude real, "
              "longitude real, feature_class text, feature_code text, country_code text, admin1_code text, "
              "population integer, elevation integer, timezone text)")
    c.execute("create virtual table places_rtree using rtree(id, minx, maxx, miny, maxy)")
    rows = []
    with zipfile.ZipFile(path) as z:
        for line in io.TextIOWrapper(z.open("US.txt"), encoding="utf-8"):
            r = dict(zip(GEO, line.rstrip("\n").split("\t")))
            if h1("geonames-us-v1:" + r["admin1_code"]) % 2 != params["parity"]:
                continue
            rows.append((int(r["geonameid"]), r["name"], r["asciiname"], float(r["latitude"]),
                         float(r["longitude"]), r["feature_class"], r["feature_code"], r["country_code"],
                         r["admin1_code"], int(r["population"] or 0), ni(r["elevation"]), r["timezone"]))
    c.executemany("insert into places values(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("insert into places_rtree select geonameid, longitude, longitude, latitude, latitude from places")
    finish(c)


LOG = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(.*)" (\d{3}|-) (\d+|-)\s*$')


def b_httplog(out, raw, params, work):
    c = newdb(out)
    c.execute("create table access(host text, ts text, method text, path text, protocol text, status integer, "
              "bytes integer)")
    rows = []
    for path, _ in raw:
        for line in gzip.open(path, "rb"):
            m = LOG.match(line.decode("latin-1").rstrip("\n"))
            if not m:
                continue
            req = m.group(3).split(" ")
            method, upath, proto = (req + [None, None, None])[:3] if len(req) >= 2 else (None, m.group(3), None)
            rows.append((m.group(1), m.group(2), method, upath, proto,
                         None if m.group(4) == "-" else int(m.group(4)),
                         None if m.group(5) == "-" else int(m.group(5))))
            if len(rows) >= params["maxrows"]:
                break
    c.executemany("insert into access values(?,?,?,?,?,?,?)", rows)
    finish(c)


GTFS_SCHEMA = """
create table agency(agency_id text primary key, agency_name text, agency_timezone text);
create table routes(route_id text primary key, agency_id text, route_short_name text, route_long_name text, route_type integer);
create table trips(trip_id text primary key, route_id text not null, service_id text not null, trip_headsign text, direction_id integer, shape_id text);
create table stops(stop_id text primary key, stop_code text, stop_name text, stop_lat real, stop_lon real, location_type integer, parent_station text);
create table stop_times(trip_id text not null, arrival_time text, departure_time text, stop_id text not null, stop_sequence integer not null, pickup_type integer, drop_off_type integer, primary key(trip_id, stop_sequence));
create table calendar(service_id text primary key, monday integer, tuesday integer, wednesday integer, thursday integer, friday integer, saturday integer, sunday integer, start_date text, end_date text);
create table calendar_dates(service_id text not null, date text not null, exception_type integer, primary key(service_id, date));
create index idx_trips_route_id on trips(route_id);
create index idx_trips_service_id on trips(service_id);
create index idx_stop_times_stop_id on stop_times(stop_id);
create index idx_calendar_dates_date on calendar_dates(date);
"""


def b_gtfs(out, raw, params, work):
    (z, _), = raw
    c = newdb(out)
    c.executescript(GTFS_SCHEMA)
    c.executemany("insert or ignore into agency values(?,?,?)", ((nz(r.get("agency_id")), r["agency_name"], r["agency_timezone"]) for r in zcsv(z, "agency.txt")))
    c.executemany("insert into routes values(?,?,?,?,?)", ((r["route_id"], nz(r.get("agency_id")), nz(r.get("route_short_name")), nz(r.get("route_long_name")), ni(r.get("route_type"))) for r in zcsv(z, "routes.txt")))
    c.executemany("insert into trips values(?,?,?,?,?,?)", ((r["trip_id"], r["route_id"], r["service_id"], nz(r.get("trip_headsign")), ni(r.get("direction_id")), nz(r.get("shape_id"))) for r in zcsv(z, "trips.txt")))
    c.executemany("insert into stops values(?,?,?,?,?,?,?)", ((r["stop_id"], nz(r.get("stop_code")), nz(r.get("stop_name")), nf(r.get("stop_lat")), nf(r.get("stop_lon")), ni(r.get("location_type")), nz(r.get("parent_station"))) for r in zcsv(z, "stops.txt")))
    c.executemany("insert into stop_times values(?,?,?,?,?,?,?)", ((r["trip_id"], nz(r.get("arrival_time")), nz(r.get("departure_time")), r["stop_id"], int(r["stop_sequence"]), ni(r.get("pickup_type")), ni(r.get("drop_off_type"))) for r in zcsv(z, "stop_times.txt")))
    try:
        c.executemany("insert into calendar values(?,?,?,?,?,?,?,?,?,?)", ((r["service_id"], int(r["monday"]), int(r["tuesday"]), int(r["wednesday"]), int(r["thursday"]), int(r["friday"]), int(r["saturday"]), int(r["sunday"]), r["start_date"], r["end_date"]) for r in zcsv(z, "calendar.txt")))
    except IndexError:
        pass
    c.executemany("insert or replace into calendar_dates values(?,?,?)", ((r["service_id"], r["date"], int(r["exception_type"])) for r in zcsv(z, "calendar_dates.txt")))
    finish(c)


def b_ghcn(out, raw, params, work):
    c = newdb(out)
    c.executescript("""create table station(station text primary key, name text, latitude real, longitude real, elevation real);
      create table daily(station text not null, date text not null, prcp integer, snow integer, snwd integer, tmax integer, tmin integer, tavg integer, primary key(station, date)) without rowid;""")
    for path, entry in sorted(raw, key=lambda item: item[1]["name"]):
        s = entry["name"].removesuffix(".csv")
        with open(path, newline="") as f:
            first, rows = None, []
            for r in csv.DictReader(f):
                if first is None:
                    first = r
                rows.append((s, r["DATE"], ni(r.get("PRCP")), ni(r.get("SNOW")), ni(r.get("SNWD")),
                             ni(r.get("TMAX")), ni(r.get("TMIN")), ni(r.get("TAVG"))))
        if not rows:
            continue
        c.execute("insert into station values(?,?,?,?,?)", (s, first.get("NAME"), nf(first.get("LATITUDE")),
                                                            nf(first.get("LONGITUDE")), nf(first.get("ELEVATION"))))
        c.executemany("insert into daily values(?,?,?,?,?,?,?,?)", rows)
    finish(c)


# ---------------- agent 3: wave 2 ----------------
def finish_w2(con):
    con.commit()
    con.execute("ANALYZE")
    con.commit()
    con.execute("VACUUM")
    con.close()


def b_ledger(out, raw, params, work):
    (path, _), = raw
    con = sqlite3.connect(out)
    con.execute("CREATE TABLE ledger(id INTEGER PRIMARY KEY, grp TEXT NOT NULL, period TEXT NOT NULL, "
                "category TEXT NOT NULL, amount TEXT NOT NULL)")
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8", newline="")):
        period = r[params["period"]]
        if params.get("periods") and period not in params["periods"]:
            continue
        for col in params["components"]:
            a = (r[col] or "").strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", a):
                rows.append((r[params["group"]].strip(), period, col, a))
    con.executemany("INSERT INTO ledger(grp, period, category, amount) VALUES(?,?,?,?)", rows)
    con.execute("CREATE INDEX ledger_grp ON ledger(grp, period, category, amount)")
    finish_w2(con)


def obs_epa(raw):
    rows = {}
    for path, _ in raw:
        z = zipfile.ZipFile(path)
        for name in z.namelist():
            with z.open(name) as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, "utf-8", newline="")):
                    if r["Event Type"] not in ("None", "Included"):
                        continue
                    try:
                        v = float(r["Arithmetic Mean"])
                    except ValueError:
                        continue
                    site = f'{r["State Code"]}-{r["County Code"]}-{r["Site Num"]}-{r["POC"]}-{r["Sample Duration"][:2]}'
                    rows.setdefault((site, r["Date Local"]), v)
    return rows


def obs_nwis(raw):
    rows = {}
    for path, _ in raw:
        cols = None
        for line in open(path, encoding="utf-8"):
            if line.startswith("#"):
                cols = None
                continue
            p = line.rstrip("\n").split("\t")
            if cols is None:
                if p[0] == "agency_cd":
                    cols = p
                    vi = [i for i, c in enumerate(cols) if c.endswith("_00060_00003")]
                    skip = True
                continue
            if skip:
                skip = False
                continue
            for i in vi:
                try:
                    v = float(p[i])
                except (ValueError, IndexError):
                    continue
                rows.setdefault((f"{p[1]}-{cols[i].split('_')[0]}", p[2]), v)
    return rows


def b_obs(out, raw, params, work):
    rows = {"epa-aqs-daily": obs_epa, "usgs-nwis-dv": obs_nwis}[params["format"]](raw)
    con = sqlite3.connect(out)
    con.execute("CREATE TABLE obs(site TEXT NOT NULL, year INTEGER NOT NULL, day TEXT NOT NULL, value REAL NOT NULL, "
                "PRIMARY KEY(site, year, day)) WITHOUT ROWID")
    con.executemany("INSERT INTO obs VALUES(?,?,?,?)", ((s, int(day[:4]), day, v) for (s, day), v in sorted(rows.items())))
    finish_w2(con)


SE_DDL = """
CREATE TABLE users(id INTEGER PRIMARY KEY, reputation INT, creation_date TEXT, display_name TEXT, last_access_date TEXT,
  website_url TEXT, location TEXT, about_me TEXT, views INT, up_votes INT, down_votes INT, account_id INT);
CREATE TABLE posts(id INTEGER PRIMARY KEY, post_type_id INT NOT NULL,
  accepted_answer_id INT REFERENCES posts(id) ON DELETE SET NULL,
  parent_id INT REFERENCES posts(id) ON DELETE CASCADE, creation_date TEXT, score INT, view_count INT, body TEXT,
  owner_user_id INT REFERENCES users(id) ON DELETE SET NULL, owner_display_name TEXT,
  last_editor_user_id INT REFERENCES users(id) ON DELETE SET NULL, last_edit_date TEXT, last_activity_date TEXT,
  title TEXT, tags TEXT, answer_count INT, comment_count INT, favorite_count INT, closed_date TEXT,
  community_owned_date TEXT, content_license TEXT);
CREATE TABLE comments(id INTEGER PRIMARY KEY, post_id INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, score INT,
  text TEXT, creation_date TEXT, user_id INT REFERENCES users(id) ON DELETE SET NULL, user_display_name TEXT, content_license TEXT);
CREATE TABLE votes(id INTEGER PRIMARY KEY, post_id INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, vote_type_id INT,
  user_id INT REFERENCES users(id) ON DELETE SET NULL, creation_date TEXT, bounty_amount INT);
CREATE TABLE post_history(id INTEGER PRIMARY KEY, post_history_type_id INT, post_id INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  revision_guid TEXT, creation_date TEXT, user_id INT REFERENCES users(id) ON DELETE SET NULL, user_display_name TEXT,
  comment TEXT, text TEXT, content_license TEXT);
CREATE TABLE post_links(id INTEGER PRIMARY KEY, creation_date TEXT, post_id INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  related_post_id INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE, link_type_id INT);
CREATE TABLE badges(id INTEGER PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT, date TEXT,
  class INT, tag_based TEXT);
CREATE TABLE tags(id INTEGER PRIMARY KEY, tag_name TEXT UNIQUE, count INT, excerpt_post_id INT REFERENCES posts(id) ON DELETE SET NULL,
  wiki_post_id INT REFERENCES posts(id) ON DELETE SET NULL);
CREATE TABLE user_tombstones(user_id INTEGER PRIMARY KEY, account_id INT, reputation INT, creation_date TEXT);
CREATE INDEX posts_parent ON posts(parent_id);
CREATE INDEX posts_accepted ON posts(accepted_answer_id);
CREATE INDEX posts_owner ON posts(owner_user_id);
CREATE INDEX posts_editor ON posts(last_editor_user_id);
CREATE INDEX comments_post ON comments(post_id);
CREATE INDEX comments_user ON comments(user_id);
CREATE INDEX votes_post ON votes(post_id);
CREATE INDEX votes_user ON votes(user_id);
CREATE INDEX post_history_post ON post_history(post_id);
CREATE INDEX post_history_user ON post_history(user_id);
CREATE INDEX post_links_post ON post_links(post_id);
CREATE INDEX post_links_related ON post_links(related_post_id);
CREATE INDEX badges_user ON badges(user_id);
CREATE INDEX tags_excerpt ON tags(excerpt_post_id);
CREATE INDEX tags_wiki ON tags(wiki_post_id);
CREATE TRIGGER comments_count_ad AFTER DELETE ON comments BEGIN
  UPDATE posts SET comment_count = comment_count - 1 WHERE id = old.post_id; END;
CREATE TRIGGER answers_count_ad AFTER DELETE ON posts WHEN old.post_type_id = 2 BEGIN
  UPDATE posts SET answer_count = answer_count - 1 WHERE id = old.parent_id; END;
CREATE TRIGGER votes_score_ad AFTER DELETE ON votes WHEN old.vote_type_id IN (2, 3) BEGIN
  UPDATE posts SET score = score - CASE old.vote_type_id WHEN 2 THEN 1 ELSE -1 END WHERE id = old.post_id; END;
CREATE TRIGGER users_tombstone_bd BEFORE DELETE ON users BEGIN
  INSERT INTO user_tombstones VALUES(old.id, old.account_id, old.reputation, old.creation_date); END;
"""
SE_TABLES = [("Users", "users", ["Id", "Reputation", "CreationDate", "DisplayName", "LastAccessDate", "WebsiteUrl", "Location", "AboutMe", "Views", "UpVotes", "DownVotes", "AccountId"]),
  ("Posts", "posts", ["Id", "PostTypeId", "AcceptedAnswerId", "ParentId", "CreationDate", "Score", "ViewCount", "Body", "OwnerUserId", "OwnerDisplayName", "LastEditorUserId", "LastEditDate", "LastActivityDate", "Title", "Tags", "AnswerCount", "CommentCount", "FavoriteCount", "ClosedDate", "CommunityOwnedDate", "ContentLicense"]),
  ("Comments", "comments", ["Id", "PostId", "Score", "Text", "CreationDate", "UserId", "UserDisplayName", "ContentLicense"]),
  ("Votes", "votes", ["Id", "PostId", "VoteTypeId", "UserId", "CreationDate", "BountyAmount"]),
  ("PostHistory", "post_history", ["Id", "PostHistoryTypeId", "PostId", "RevisionGUID", "CreationDate", "UserId", "UserDisplayName", "Comment", "Text", "ContentLicense"]),
  ("PostLinks", "post_links", ["Id", "CreationDate", "PostId", "RelatedPostId", "LinkTypeId"]),
  ("Badges", "badges", ["Id", "UserId", "Name", "Date", "Class", "TagBased"]),
  ("Tags", "tags", ["Id", "TagName", "Count", "ExcerptPostId", "WikiPostId"])]
SE_INTS = {"Id", "Reputation", "Views", "UpVotes", "DownVotes", "AccountId", "PostTypeId", "AcceptedAnswerId", "ParentId",
           "Score", "ViewCount", "OwnerUserId", "LastEditorUserId", "AnswerCount", "CommentCount", "FavoriteCount",
           "PostId", "UserId", "VoteTypeId", "BountyAmount", "PostHistoryTypeId", "RelatedPostId", "LinkTypeId",
           "Class", "Count", "ExcerptPostId", "WikiPostId"}
SE_NAMES = {"posts": "id, post_type_id, accepted_answer_id, parent_id, creation_date, score, view_count, body, owner_user_id, owner_display_name, last_editor_user_id, last_edit_date, last_activity_date, title, tags, answer_count, comment_count, favorite_count, closed_date, community_owned_date, content_license",
            "tags": "id, tag_name, count, excerpt_post_id, wiki_post_id"}


def b_stackexchange(out, raw, params, work):
    (archive, _), = raw
    xml = work / "xml"
    xml.mkdir()
    subprocess.run(["7z", "x", "-t7z", "-y", "-o" + str(xml), str(archive)], check=True,
                   stdout=subprocess.DEVNULL)
    con = sqlite3.connect(out)
    con.executescript("PRAGMA page_size=4096;" + SE_DDL.split("CREATE INDEX")[0])
    for name, tab, cols in SE_TABLES:
        rows = []
        for _, el in ET.iterparse(xml / (name + ".xml")):
            if el.tag != "row":
                continue
            a = el.attrib
            rows.append(tuple((int(a[c]) if c in SE_INTS and a.get(c) not in (None, "") else a.get(c)) for c in cols))
            el.clear()
        names = SE_NAMES.get(tab)
        con.executemany(f"INSERT INTO {tab}{'(' + names + ')' if names else ''} VALUES({','.join('?' * len(cols))})", rows)
    con.executescript("""
UPDATE posts SET parent_id = NULL WHERE parent_id IS NOT NULL AND parent_id NOT IN (SELECT id FROM posts) AND post_type_id <> 2;
DELETE FROM posts WHERE post_type_id = 2 AND (parent_id IS NULL OR parent_id NOT IN (SELECT id FROM posts));
UPDATE posts SET parent_id = NULL WHERE parent_id IS NOT NULL AND parent_id NOT IN (SELECT id FROM posts);
UPDATE posts SET accepted_answer_id = NULL WHERE accepted_answer_id IS NOT NULL AND accepted_answer_id NOT IN (SELECT id FROM posts);
UPDATE posts SET owner_user_id = NULL WHERE owner_user_id IS NOT NULL AND owner_user_id NOT IN (SELECT id FROM users);
UPDATE posts SET last_editor_user_id = NULL WHERE last_editor_user_id IS NOT NULL AND last_editor_user_id NOT IN (SELECT id FROM users);
DELETE FROM comments WHERE post_id NOT IN (SELECT id FROM posts);
UPDATE comments SET user_id = NULL WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users);
DELETE FROM votes WHERE post_id NOT IN (SELECT id FROM posts);
UPDATE votes SET user_id = NULL WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users);
DELETE FROM post_history WHERE post_id NOT IN (SELECT id FROM posts);
UPDATE post_history SET user_id = NULL WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM users);
DELETE FROM post_links WHERE post_id NOT IN (SELECT id FROM posts) OR related_post_id NOT IN (SELECT id FROM posts);
DELETE FROM badges WHERE user_id NOT IN (SELECT id FROM users);
UPDATE tags SET excerpt_post_id = NULL WHERE excerpt_post_id IS NOT NULL AND excerpt_post_id NOT IN (SELECT id FROM posts);
UPDATE tags SET wiki_post_id = NULL WHERE wiki_post_id IS NOT NULL AND wiki_post_id NOT IN (SELECT id FROM posts);
""")
    con.executescript("CREATE INDEX" + "CREATE INDEX".join(SE_DDL.split("CREATE INDEX")[1:]))
    con.commit()
    if con.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Stack Exchange referential cleanup incomplete")
    con.execute("ANALYZE")
    con.commit()
    con.execute("VACUUM")
    con.close()


BUILDERS = {
    "geonames-integrity": b_geonames_integrity, "pageviews": b_pageviews, "zip-member": b_zip_member,
    "bunzip2": b_bunzip2, "usgs-geojson": b_usgs, "storm-events": b_storms,
    "geonames-rtree": b_geonames_rtree, "http-access-log": b_httplog, "gtfs": b_gtfs, "ghcn-daily": b_ghcn,
    "payroll-ledger": b_ledger, "daily-observations": b_obs, "stackexchange": b_stackexchange,
}


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def authentic(path, entry):
    return path.is_file() and path.stat().st_size == entry["bytes"] and sha256(path) == entry["sha256"]


def build(job):
    entry, raw_root, files, emit = job
    recipe = entry["derive"]
    raw = []
    for item in recipe["raw"]:
        path = Path(raw_root) / item["sha256"]
        if not authentic(path, item):
            raise ValueError("Raw upstream input missing or changed: " + item["name"])
        raw.append((path, item))
    with tempfile.TemporaryDirectory(prefix="derive-", dir=files) as temporary:
        work = Path(temporary)
        out = work / "out"
        BUILDERS[recipe["builder"]](str(out), raw, recipe.get("params", {}), work)
        got = {"bytes": out.stat().st_size, "sha256": sha256(out)}
        if not emit and (got["bytes"], got["sha256"]) != (entry["bytes"], entry["sha256"]):
            raise ValueError(f"Derived input differs from its commitment: {entry['name']} {got}")
        os.replace(out, Path(files) / got["sha256"])
    return entry["name"], got


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recipes", type=Path, help="derive recipe JSON or an inputs manifest with file recipes")
    parser.add_argument("files", type=Path)
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--emit", action="store_true")
    args = parser.parse_args()
    value = json.loads(args.recipes.read_text())
    if "splits" in value:  # inputs manifest: every listed case file is kept
        entries, keep = [], set()
        for batches in value["splits"].values():
            for batch in batches:
                for doc in batch["docs"]:
                    keep.add(doc["sha256"])
                    if "requests_sha256" in doc:
                        keep.add(doc["requests_sha256"])
                    for entry in doc.get("files", []):
                        keep.add(entry["sha256"])
                        entries.append(entry)
    else:
        entries, keep = value["outputs"], set(value["keep"])
        keep.update(entry["sha256"] for entry in entries)
    jobs = [(entry, args.raw or args.files, args.files, args.emit) for entry in entries
            if "derive" in entry and (args.emit or not authentic(args.files / entry["sha256"], entry))]
    with multiprocessing.Pool(max(1, args.jobs)) as pool:
        results = dict(pool.imap_unordered(build, jobs))
    if not args.emit:
        for entry in entries:
            if not authentic(args.files / entry["sha256"], entry):
                raise ValueError("Missing or changed case input: " + entry["name"])
        for sha in keep:
            if not (args.files / sha).is_file():
                raise ValueError("Missing retained case input: " + sha)
    if args.prune:
        for path in args.files.iterdir():
            if path.name not in keep:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
    print(json.dumps(results, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
