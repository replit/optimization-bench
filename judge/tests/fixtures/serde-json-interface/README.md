This is the byte-identical original `src/ser.rs` from serde-rs/json commit
`20572a6d066958542d79cdd6cf1a7cd9f0c8e1f8`, used by the factory's serde-json task.
The accompanying original MIT/Apache licenses are retained.

Archive: https://codeload.github.com/serde-rs/json/tar.gz/20572a6d066958542d79cdd6cf1a7cd9f0c8e1f8

Archive SHA-256: `d638770aea490d6b9b94b67fa14c3533778431a8715792b2fac80e7ca405d721`

Original ser.rs SHA-256: `f2b8cc0b97e30c49dbc58698fa22c14a7d56ea616cdb57b067835a59af49a75f`

The offline regression changes only `pub fn to_writer<` to private or
`pub(crate)` in a temporary candidate. Lifetime apostrophes previously caused
C quoting rules to swallow this declaration and falsely accept both controls.
