# GH Archive acquisition

GH Archive offers complete hourly GitHub event archives for download and analysis:
https://www.gharchive.org/

GH Archive event data is fetch-only in this benchmark and is not redistributed.
No GH Archive event body, input packet or event-derived output body is stored in
this repository or in the task image; only download URLs, sizes and SHA-256
commitments are stored, plus length and SHA-256 commitments for derived outputs.
The GH Archive project's code license and website license are not asserted to
license the event data.

Public inputs use authenticated upstream URL-plus-SHA256 recipes. Held-out inputs
are downloaded the same way by the verifier, outside Git. The public and
held-out hours are different days from the same publisher, not different publishers.
Event identities were checked for zero overlap. Repository/actor disjointness
is not claimed.

Gzip decoding is bounded and checked against a second size/SHA256 commitment
before each measured process starts. Each operation consumes a complete original
hour once. The two operations may use the same hour but execute independently.

Public preparation downloads its hour into an authenticated external packet.
The image build reads public inputs through transient read-only mounts, not
body-containing COPY layers. Runtime setup supplies the same authenticated
files outside timing. Do not redistribute input packets, extracted event bodies
or derived output bodies without separately establishing the required rights.
Publishing acquisition recipes, integrity commitments or a separately approved
body-free image does not grant rights to redistribute the underlying data.
