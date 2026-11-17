# Externally visible interface projection

The gate compares callable/public API facts, not implementation representation:
argument bindings, stack frames, private fields and private archive COMDAT
copies are not independently promised interfaces. Public items may be added;
existing public calls, fields and constructors must remain usable.

Projection identity is `externally-visible-projection-v3`. It salts the
interface fingerprint and the rebuilt-reference descriptor/cache key. A cached
report without this version is not a current projection and must be regenerated.

## Other language adapters

Go source projection includes exported fields/methods, local type aliases and
promoted selectors, excludes unexported members and test-only source, and
retains imported package identity rather than a local import alias. Ambiguous
promoted selectors are not falsely advertised as callable. It is source
projection, not `go/types` compiler proof; opaque type/constant expressions
remain conservative.

Python's real package probe honors explicit `__all__`, documentation and
extension-point scope. Owned reexports through private modules stay public;
explicit immutable foreign reexports are bound rather than silently dropped.
Candidate checks retain public alias relationships. Private/non-advertised names
do not expand the contract, and class public fields remain protected.

JS/TS static adapters omit `private` and `#private` members while retaining
public fields and subclass-visible protected members. Narrowing public access,
removing a setter/writable field, or hiding a public constructor is breaking;
private-constructor argument changes are not external API changes. Runtime JS
graphs already omit inaccessible private slots and retain public alias graphs.

Java private-owner descendants are not accidentally public. Public/protected
overloads and fields use stable member IDs, and an explicit equivalent default
constructor no longer looks like removal of the implicit one.

All adapters report added entries and paired `changed_details` with old/new
descriptors. C/C++ overloads use stable named declaration identities without
loosening signature/inline/ABI checks; jq's guarded inline change still fails.

## Rust

`rust_surface.py` follows Cargo library roots, inline/out-of-line modules,
private import aliases, public aliases/globs and local workspace/path
dependencies. A reexported type's inherent methods are included, so methods
reachable only through a root reexport are not silently missed. Local non-generic type aliases also expose target members,
without inventing an inaccessible tuple/unit alias constructor.

Only bare `pub` fields/methods/items are external exports. Private,
`pub(crate)`, `pub(super)` and `pub(in ...)` items are excluded. Tuple indices
remain their real indices; record fields retain names/types. Public constructors
are represented separately, so adding a private field to an all-public record
cannot silently remove literal construction. Public trait members and enum
variants remain protected. Exported macro matcher shapes are retained, not
their private expansion bodies.

Rows use stable public paths, with old/new signature details for changes.
Nominal alias relationships and referenced types are compared through public
paths, not a chosen private definition spelling. Moving an implementation or
adding a type alias does not by itself break its existing call signatures.
String literals are never erased by type-name normalization.

This is grammar-backed source projection, **not compiler-proven completeness**:
diagnostics expose immutable external reexports and unresolved/generated
modules. It does not claim macro expansion, a Rust trait solver, or complete
ABI equivalence. Protected consumer and semantic/fault probes remain necessary.

## Native archives

Dynamic exports and strong symbols remain protected. Static W/V entities are
not all public merely because `nm -g` lists a compiler-emitted copy.
`native_surface.py` excludes only recognized Itanium C++ vague-linkage
entity/owner names absent from expanded, public-origin header text.

Real public weak entities, C weak symbols, allocation ABI hooks and unknown
mangling remain protected. Missing/raw-only header proof preserves all symbols.
Private/system headers are not promoted by guessing directory names; transitive
public header directories can be explicitly declared with
`public_include_roots`. Public header-consumer compile/link checks remain a
separate requirement, including GNU89 behavior.
