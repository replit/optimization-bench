# Optional repair overlay for the pinned Jazzer4 addon GLIBC_2.38 requirement.
# TASK_IMAGE here is the immutable javascript runner image, whose own image.json
# binds the original task image. Node and the task libc are NOT replaced.
ARG TASK_IMAGE
FROM ${TASK_IMAGE}
USER root
RUN apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends python3 cmake g++ make patch ca-certificates \
 && dpkg-query -W > /opt/fuzz-os-packages.lock && rm -rf /var/lib/apt/lists/*
RUN python3 -c "import urllib.request,hashlib,pathlib; p=pathlib.Path('/tmp/jazzer-source.tar.gz'); p.write_bytes(urllib.request.urlopen('https://codeload.github.com/CodeIntelligenceTesting/jazzer.js/tar.gz/f3eb99cb0ea20fe45356535b1f60f86741974e72').read()); assert hashlib.sha256(p.read_bytes()).hexdigest() == '2c8c23ffb3d12ae3d0171ed6ee15c51a4ab0a0b5bf53d9474819877368eb3900'" \
 && mkdir /tmp/jazzer-source && tar -xzf /tmp/jazzer-source.tar.gz -C /tmp/jazzer-source --strip-components=1 \
 && cmake -S /tmp/jazzer-source/packages/fuzzer -B /tmp/jazzer-build \
      -DCMAKE_BUILD_TYPE=Release \
      '-DCMAKE_JS_INC=/usr/local/include/node;/opt/fuzz-runners/node_modules/node-addon-api' \
 && cmake --build /tmp/jazzer-build -j2 \
 && cp /tmp/jazzer-build/jazzerjs.node /opt/fuzz-runners/node_modules/@jazzer.js/fuzzer/prebuilds/fuzzer-linux-x64.node \
 && cp /tmp/jazzer-source/packages/fuzzer/CMakeLists.txt /opt/fuzz-runners/jazzer-source-CMakeLists.txt \
 && cp /tmp/jazzer-source/LICENSE /opt/fuzz-runners/jazzer-source-LICENSE \
 && sha256sum /opt/fuzz-runners/node_modules/@jazzer.js/fuzzer/prebuilds/fuzzer-linux-x64.node > /opt/fuzz-runners/jazzer-addon.sha256 \
 && rm -rf /tmp/jazzer-source /tmp/jazzer-build /tmp/jazzer-source.tar.gz
# CMake upstream also hash-pins its patched compiler-rt archive:
# SHA256 200b32c897b1171824462706f577d7f1d6175da602eccfe570d2dceeac11d490.
WORKDIR /work
