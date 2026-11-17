# Stack Exchange content (fkey-cascade unit)

The public foreign-key-cascade input uses a database and request list adapted from the
Stack Exchange Data Dump (2024-04 release, Internet Archive item
https://archive.org/details/stackexchange), published by Stack Exchange, Inc.
Held-out inputs are not part of this image; their attribution ships with the verifier, in
the task's `tests/notices/` directory.

## Sources and attribution

Content by the contributors of this Stack Exchange site:

- **Seasoned Advice (Cooking Stack Exchange) contributors**, https://cooking.stackexchange.com/
  Archive: https://archive.org/download/stackexchange/cooking.stackexchange.com.7z
  (87,555,311 bytes, SHA-256 `de98b85f9807a23dfc8c91006f8b59b2149089e94ea9d54676d215ea156a9ac6`).
  The image build derives `se.db` from this archive; the public request list
  (`fkey-cascade-requests-public`, SHA-256 `73376af59af0ef9af69611eb65406e58d4883344d1bf1c36df100c5440b9cd12`)
  is stored in Git.

Every post, comment and revision keeps its original identifiers and author fields, so it
can be attributed to its source: question `<id>` is `https://cooking.stackexchange.com/q/<id>`,
answer `<id>` is `https://cooking.stackexchange.com/a/<id>`, user `<id>` is
`https://cooking.stackexchange.com/users/<id>`, and author names are
in `users.display_name`, `posts.owner_display_name`, `comments.user_display_name` and
`post_history.user_display_name`. The data dump's own attribution statement is retained
verbatim in `suite-inputs/stackexchange-data-dump-license.txt`.

## Licences

Stack Exchange user contributions are licensed by contribution date
(https://cooking.stackexchange.com/help/licensing,
https://stackoverflow.com/legal/terms-of-service/public):

- contributed before 2011-04-08 (UTC): CC BY-SA 2.5,
  https://creativecommons.org/licenses/by-sa/2.5/ (legal code: `suite-inputs/cc-by-sa-2.5-legalcode.html`);
- contributed from 2011-04-08 up to but not including 2018-05-02 (UTC): CC BY-SA 3.0,
  https://creativecommons.org/licenses/by-sa/3.0/ (legal code: `suite-inputs/cc-by-sa-3.0-legalcode.txt`);
- contributed on or after 2018-05-02 (UTC): CC BY-SA 4.0,
  https://creativecommons.org/licenses/by-sa/4.0/ (legal code: `suite-inputs/cc-by-sa-4.0-legalcode.txt`).

The licence of each post, comment and post revision is kept unchanged in the
`content_license` column of the `posts`, `comments` and `post_history` tables.

The derived database and request list are adaptations of this content and are
licensed under **CC BY-SA 4.0** (https://creativecommons.org/licenses/by-sa/4.0/). CC BY-SA
2.5 (section 4(b)) and CC BY-SA 3.0 (section 4(b)) allow adaptations to be licensed
under a later version of the licence with the same licence elements.

## Changes made

- The XML files of the site archive were loaded into SQLite tables (`users`, `posts`,
  `comments`, `votes`, `post_history`, `post_links`, `badges`, `tags`) with declared
  foreign keys and indexes. Only the columns listed in `SE_TABLES` of
  `workload/driver/derive.py` were loaded; integer columns were stored as integers.
- Referential cleanup: answers whose question is missing; comments, votes, post history
  and post links that refer to missing posts; and badges of missing users were deleted.
  Other references to missing posts or users were set to NULL.
- `ANALYZE` and `VACUUM` were run.
- The request list selects user and post identifiers from this database.
- No post, comment or revision text was edited.

## Share-alike

If you share this database, request list, or material adapted from it (including
workload outputs that contain its content), you must license it under CC BY-SA 4.0 (or
a licence that section 3(b)(1) of CC BY-SA 4.0 allows), keep this attribution, link the
licence, and state your changes. The content is provided as-is, without warranties (see
section 5 of the licence). Neither Stack Exchange, Inc. nor the contributors endorse this
benchmark.
