# Protected Goldmark corpus attribution

The 12,581 protected Markdown inputs are whole documentation pages from the
repositories below, at the exact commits recorded per document in
`../heldout/manifest.json`. Original paths, Git blobs, hashes, byte lengths,
licenses, and any front-matter transformation are retained there. Original
text is unchanged except for the manifest-declared `strip-front-matter-v1`
transformation on the added documentation families. The Kubernetes documents
retain their original bytes.

These are redistributable source materials, not confidential customer data.
Keep this attribution, the verbatim license texts in `licenses/`, and per-file
provenance together with every protected-input distribution. Complete expected
HTML outputs are derived renderings of those same documents and retain their
source licensing and attribution. They are frozen verifier artifacts, not a
license to regenerate the benchmark's expectations.

| Source repository | Pinned commit | License and retained text |
| --- | --- | --- |
| [kubernetes/website](https://github.com/kubernetes/website) | `ed70e311737a341574ed5758b7af24e3bba232cb` | CC-BY-4.0; `licenses/kubernetes--website/LICENSE` |
| [cncf/glossary](https://github.com/cncf/glossary) | `c6493fa4824c440cca36a970f9ca68eeabc544bb` | Apache-2.0; `licenses/cncf--glossary/LICENSE` |
| [crossplane/docs](https://github.com/crossplane/docs) | `744f565042b087af55d50eaa5c9d8942be134649` | CC-BY-4.0; `licenses/crossplane--docs/LICENSE.md` |
| [etcd-io/website](https://github.com/etcd-io/website) | `02d702b09d548b1a11016d8a02d36e255f358edd` | CC-BY-4.0 content / Apache-2.0 code; `licenses/etcd-io--website/LICENSE` |
| [falcosecurity/falco-website](https://github.com/falcosecurity/falco-website) | `f17565c6f0e097a78c886351d36e75ad36ee40a6` | CC-BY-4.0; `licenses/falcosecurity--falco-website/LICENSE` |
| [influxdata/docs-v2](https://github.com/influxdata/docs-v2) | `c3a3cace199e5e7b915dc8914f58c7e81096ddb5` | MIT; `licenses/influxdata--docs-v2/LICENSE` |
| [istio/istio.io](https://github.com/istio/istio.io) | `2100f4033a629dd1986620fe1f7c3375e73945e1` | Apache-2.0; `licenses/istio--istio.io/LICENSE` |
| [jaegertracing/documentation](https://github.com/jaegertracing/documentation) | `231b67c02d3303b8286748a2be7cb924ac73a5af` | Apache-2.0; `licenses/jaegertracing--documentation/LICENSE` |
| [kedacore/keda-docs](https://github.com/kedacore/keda-docs) | `73af3b22110b765a850064b3162fc27ac0f4ff33` | Apache-2.0; `licenses/kedacore--keda-docs/LICENSE` |
| [linkerd/website](https://github.com/linkerd/website) | `95fddf116545273ffd0c12309081f17c9496ffa4` | Apache-2.0; `licenses/linkerd--website/LICENSE` |
| [nginx/documentation](https://github.com/nginx/documentation) | `d02fa31b50f5aa93f36313fe72f36da5d6933278` | BSD-2-Clause; `licenses/nginx--documentation/LICENSE` |
| [pulumi/docs](https://github.com/pulumi/docs) | `fdddd79df31b9fe8ec1b1b3865ead3e87d9a39a6` | Apache-2.0; `licenses/pulumi--docs/LICENSE` |
| [tektoncd/website](https://github.com/tektoncd/website) | `89927b1c9738e18a19b964005d386af8249820bf` | Apache-2.0; `licenses/tektoncd--website/LICENSE` |

Twelve license files were retained byte-for-byte from the source snapshot's
corpus-selection materials. The Kubernetes license was obtained anonymously
from its pinned commit:
`https://raw.githubusercontent.com/kubernetes/website/ed70e311737a341574ed5758b7af24e3bba232cb/LICENSE`,
SHA-256 `9ba9550ad48438d0836ddab3da480b3b69ffa0aac7b7878b5a0039e7ab429411`.

## Creator credits removed with the front matter

The removed front matter can carry creator credits, such as page or post
`author` fields. Those credits remain in the original document. Each derived
input names its original in `../heldout/manifest.json` through
`provenance.repo`, `provenance.commit` and `provenance.source_path`, and the
original is at `https://github.com/<repo>/blob/<commit>/<source_path>`. For the
CC-BY-4.0 families that were transformed:

| Repository | Original document URL pattern |
| --- | --- |
| crossplane/docs | `https://github.com/crossplane/docs/blob/744f565042b087af55d50eaa5c9d8942be134649/<source_path>` |
| etcd-io/website | `https://github.com/etcd-io/website/blob/02d702b09d548b1a11016d8a02d36e255f358edd/<source_path>` |
| falcosecurity/falco-website | `https://github.com/falcosecurity/falco-website/blob/f17565c6f0e097a78c886351d36e75ad36ee40a6/<source_path>` |

The other families follow the same pattern with the commit in the table above.
For CC-BY-4.0 content, this link to the original document is how the creator
attribution is provided: CC BY 4.0 Section 3(a)(2) allows the attribution
conditions to be satisfied by a URI or hyperlink to a resource that includes
the required information. Removing the front matter is a modification, so each
transformed input is a modified version of the linked original, licensed under
the same licence. The kubernetes/website documents were not transformed.

These held-out notices stay outside `environment/` so the agent image does not
include held-out corpus families or paths. Existing
public-corpus and Goldmark dependency notices remain in the task's public
`notices/` and the ordinary image's source/module trees.
