/* w2-fkey-cascade driver (SQLite agent 3). Given a Stack Exchange site database with declared foreign keys
 * (ON DELETE CASCADE / SET NULL), child-key indexes and counter-maintenance triggers, a moderation job purges
 * questions (cascading to answers, comments, votes, revision history and links; triggers maintain answer/comment
 * counts and scores) and dormant accounts (SET NULL on authored content, CASCADE on badges, BEFORE DELETE tombstone
 * trigger). PRAGMA foreign_keys=ON. One transaction per request. The database is an application-owned in-memory
 * image (sqlite3_deserialize, staged before timing), so no file I/O occurs in the timed region.
 * Per request output: kind, id, sqlite3_changes64 and the sqlite3_total_changes64 delta (includes trigger and FK
 * action rows). After the timed region: a canonical key/counter dump of every table (verification only). */
#include "w2.h"
static const char *DUMP[] = {
  "SELECT id, parent_id, accepted_answer_id, owner_user_id, last_editor_user_id, answer_count, comment_count, score FROM posts ORDER BY id",
  "SELECT id, post_id, user_id FROM comments ORDER BY id", "SELECT id, post_id, user_id FROM votes ORDER BY id",
  "SELECT id, post_id, user_id FROM post_history ORDER BY id", "SELECT id, post_id, related_post_id FROM post_links ORDER BY id",
  "SELECT id, user_id FROM badges ORDER BY id", "SELECT id FROM users ORDER BY id",
  "SELECT id, count, excerpt_post_id, wiki_post_id FROM tags ORDER BY id", "SELECT * FROM user_tombstones ORDER BY user_id",
  "PRAGMA foreign_key_check", 0 };
int main(int argc, char **argv) {
  unsigned long n; int mode; Request *r = load_requests(argc, argv, 2, &n, &mode);
  sqlite3 *db = open_mem_copy(argv[1]);
  exec_ok(db, "PRAGMA foreign_keys = ON");
  exec_ok(db, "SELECT count(*) FROM sqlite_schema");   /* schema load is connection staging */
  Buffer out = {0}; Region g;
  region_begin(&g, mode);
  sqlite3_stmt *begin = prepare(db, "BEGIN IMMEDIATE"), *commit = prepare(db, "COMMIT");
  sqlite3_stmt *dp = prepare(db, "DELETE FROM posts WHERE id = ?1"), *du = prepare(db, "DELETE FROM users WHERE id = ?1");
  for (unsigned long i = 0; i < n; i++) {
    sqlite3_stmt *s = !strcmp(r[i].f[0], "post") ? dp : !strcmp(r[i].f[0], "user") ? du : 0;
    if (!s) fail("request kind");
    sqlite3_int64 t0 = sqlite3_total_changes64(db);
    if (sqlite3_step(begin) != SQLITE_DONE) fail(sqlite3_errmsg(db)); sqlite3_reset(begin);
    bind_int(s, "?1", r[i].f[1]);
    if (sqlite3_step(s) != SQLITE_DONE) fail(sqlite3_errmsg(db));
    sqlite3_int64 ch = sqlite3_changes64(db);
    reuse(s);
    if (sqlite3_step(commit) != SQLITE_DONE) fail(sqlite3_errmsg(db)); sqlite3_reset(commit);
    bytes(&out, r[i].f[0], strlen(r[i].f[0])); bytes(&out, r[i].f[1], strlen(r[i].f[1]));
    u64(&out, (uint64_t)ch); u64(&out, (uint64_t)(sqlite3_total_changes64(db) - t0));
  }
  finish(begin); finish(commit); finish(dp); finish(du);
  region_end(&g);
  for (int k = 0; DUMP[k]; k++) { sqlite3_stmt *s = prepare(db, DUMP[k]); drain(db, s, &out); finish(s); }
  /* Suite packaging: export the final database image (after the timed region) so the verifier's independent
   * pristine SQLite can check integrity and a canonical logical dump of every table. */
  sqlite3_int64 isz = 0; unsigned char *img = sqlite3_serialize(db, "main", &isz, 0);
  if (!img || isz <= 0) fail("serialize");
  { char path[4096]; snprintf(path, sizeof path, "%s.db", argv[3]); Buffer ib = {img, (size_t)isz, (size_t)isz};
    FILE *f = fopen(path, "wb"); if (!f || fwrite(ib.p, 1, ib.n, f) != ib.n || fclose(f)) fail("image export"); }
  sqlite3_free(img);
  if (sqlite3_close(db) != SQLITE_OK) fail("close");
  export_buffer(argv[3], &out); free_requests(r, n);
  return 0;
}
