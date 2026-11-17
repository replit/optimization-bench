// Verifier-only bounded identity graph. No full callable text is retained.
const fs = require("node:fs");
const crypto = require("node:crypto");
const { pathToFileURL } = require("node:url");
const { builtinModules } = require("node:module");
const acorn = require("./interface_acorn.cjs");
const functionSource = Function.call.bind(Function.prototype.toString);
const ownNames = Object.getOwnPropertyNames;
const descriptor = Object.getOwnPropertyDescriptor;
const prototypeOf = Object.getPrototypeOf;
const sha = text => crypto.createHash("sha256").update(text).digest("hex");
const encode = parts => JSON.stringify(parts);
const publicNames = value => ownNames(value).filter(name => !name.startsWith("#") && !name.startsWith("_")).sort();
const identity = value => value !== null && (typeof value === "object" || typeof value === "function");

function formalShape(source, length) {
  if (/^\s*function[^(\r\n]*\([^)]*\)\s*\{\s*\[native code\]\s*\}\s*$/.test(source))
    return { opaque_native_arity: length };
  const options = {ecmaVersion: "latest", sourceType: "module", allowAwaitOutsideFunction: true};
  const privateNames = new Set();
  try {
    const tokenizer = acorn.tokenizer(source, options);
    for (let token = tokenizer.getToken(); token.type.label !== "eof"; token = tokenizer.getToken())
      if (token.type.label === "privateId") privateNames.add(token.value);
  } catch (_) {}
  const privateMethod = source.match(/^\s*#([A-Za-z_$][\w$]*)\s*\(/);
  if (privateMethod) privateNames.delete(privateMethod[1]);
  const fields = [...privateNames].sort().map(name => "#" + name + ";").join("");
  const choices = [[source, false], ["(" + source + ")", false], ["({" + source + "})", false],
    ["class __Probe extends Object{" + fields + source + "}", true],
    ["class __Probe{" + fields + "__callable=(" + source + ");}", true]];
  let callable, kind, text;
  for (const [candidate, synthetic] of choices) {
    try {
      const tree = acorn.parse(candidate, options);
      const pending = [tree];
      while (pending.length) {
        const node = pending.pop();
        if (!node || typeof node !== "object") continue;
        if (synthetic && node.type === "ClassDeclaration") {
          pending.push(...node.body.body.slice().reverse());
          continue;
        }
        if (["ClassDeclaration", "ClassExpression"].includes(node.type)) {
          const constructor = node.body.body.find(member => member.kind === "constructor");
          callable = constructor ? constructor.value : {params: [], async: false, generator: false};
          kind = constructor ? "class" : "implicit_class";
          break;
        }
        if (["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"].includes(node.type)) {
          callable = node; kind = node.type === "ArrowFunctionExpression" ? "arrow" : "function"; break;
        }
        if (node.type === "MethodDefinition" || node.type === "Property" && node.method) {
          callable = node.value; kind = ["get", "set", "constructor"].includes(node.kind) ? node.kind : "method"; break;
        }
        for (const key of Object.keys(node).sort().reverse()) {
          const value = node[key];
          if (Array.isArray(value)) pending.push(...value.slice().reverse());
          else if (value && typeof value === "object") pending.push(value);
        }
      }
      if (callable) {text = candidate; break;}
    } catch (_) {}
  }
  if (!callable) throw new Error("Cannot parse public callable formal parameters");
  function expressionHash(node) {
    const hash = crypto.createHash("sha256");
    const tokenizer = acorn.tokenizer(text.slice(node.start, node.end), options);
    for (let token = tokenizer.getToken(); token.type.label !== "eof"; token = tokenizer.getToken()) {
      let value = token.value;
      if (typeof value === "bigint") value = ["bigint", value.toString()];
      if (token.type.label === "regexp") value = [value.pattern, value.flags];
      hash.update(JSON.stringify([token.type.label, value]) + "\n");
    }
    return hash.digest("hex");
  }
  function parameter(node) {
    if (!node) return null; // Array-pattern holes remain significant.
    if (node.type === "Identifier") return "$";
    if (node.type === "AssignmentPattern") return ["default", parameter(node.left), expressionHash(node.right)];
    if (node.type === "RestElement") return ["rest", parameter(node.argument)];
    if (node.type === "ArrayPattern") return ["array", node.elements.map(parameter)];
    if (node.type === "ObjectPattern") return ["object", node.properties.map(property =>
      property.type === "RestElement" ? parameter(property) : [
        property.computed ? expressionHash(property.key) : property.key.name ?? property.key.value,
        parameter(property.value)])];
    throw new Error("Unsupported public formal parameter pattern: " + node.type);
  }
  const names = callable.params.map(node => {
    while (node && ["AssignmentPattern", "RestElement"].includes(node.type))
      node = node.left || node.argument;
    return node?.type === "Identifier" ? node.name : null;
  });
  return {kind, parameters: callable.params.map(parameter), parameter_names: names,
          async: Boolean(callable.async), generator: Boolean(callable.generator)};
}

// Build the core identity catalog BEFORE importing candidate code. Never invoke
// accessors. In particular, stream/EventEmitter internals are opaque leaves.
const builtins = new WeakMap();
const coreQueue = [];
function core(value, name, depth = 0) {
  if (!identity(value) || builtins.has(value)) return;
  builtins.set(value, name);
  coreQueue.push([value, name, depth]);
}
core(Object.prototype, "global:Object.prototype");
core(Function.prototype, "global:Function.prototype");
for (const name of ["Object", "Function", "Array", "Map", "Set", "WeakMap", "WeakSet",
  "Promise", "Date", "RegExp", "ArrayBuffer", "DataView", "Error", "TypeError",
  "Uint8Array", "Int8Array", "Uint16Array", "Int16Array", "Uint32Array", "Int32Array",
  "BigInt64Array", "BigUint64Array", "Float32Array", "Float64Array"]) core(globalThis[name], "global:" + name);
const streams = require("node:stream");
for (const name of ["Stream", "Readable", "Writable", "Duplex", "Transform", "PassThrough"])
  core(streams[name], "node:stream." + name);
core(require("node:events").EventEmitter, "node:events.EventEmitter");
for (const name of [...new Set(builtinModules.map(name => name.replace(/^node:/, "")))].sort()) {
  try {core(require("node:" + name), "node:" + name);} catch (_) {}
}
for (let index = 0; index < coreQueue.length && index < 30000; index++) {
  const [value, name, depth] = coreQueue[index];
  if (depth >= 8) continue;
  for (const key of publicNames(value)) {
    const property = descriptor(value, key);
    if ("value" in property) core(property.value, name + "." + key, depth + 1);
    else {
      if (property.get) core(property.get, name + "." + key + ".get", depth + 1);
      if (property.set) core(property.set, name + "." + key + ".set", depth + 1);
    }
  }
  core(prototypeOf(value), name + ".[[Prototype]]", depth + 1);
}

(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const limits = {max_depth: 12, max_nodes: 20000, ...(config.graph_limits || {})};
  if (!Number.isInteger(limits.max_depth) || limits.max_depth < 0 || limits.max_depth > 32 ||
      !Number.isInteger(limits.max_nodes) || limits.max_nodes < 2 || limits.max_nodes > 50000)
    throw new Error("Invalid verifier interface graph limits");
  const root = process.argv[3] || "/candidate";
  const result = {};
  const meta = result.$meta = {format: "javascript-public-graph-v3", scope: "public-api-v1",
                              compatibility: "backward-call-v1", limits, truncated: false};
  const visited = new WeakMap();
  const exposedClasses = new WeakSet(), classPrototypes = new WeakSet(), callableCache = new WeakMap();
  function callableInfo(value) {
    if (!callableCache.has(value)) {
      const source = functionSource(value);
      callableCache.set(value, {source_sha256: sha(source), formal_shape: formalShape(source, value.length)});
    }
    return callableCache.get(value);
  }
  function classLike(value) {
    if (typeof value !== "function" || builtins.has(value)) return false;
    const shape = callableInfo(value).formal_shape;
    const proto = descriptor(value, "prototype")?.value;
    return ["class", "implicit_class"].includes(shape.kind) ||
      proto && publicNames(proto).some(name => name !== "constructor");
  }
  let count = 0, aliases = 0;
  function truncate(row, reason, names = []) {
    row.truncated = {reason, frontier_sha256: sha(JSON.stringify(names))};
    meta.truncated = true;
  }
  function room(row, names) {
    if (count < limits.max_nodes) return true;
    truncate(row, "node_limit", names);
    return false;
  }
  function put(path, row) {
    const key = encode(path);
    if (Object.hasOwn(result, key)) throw new Error("Duplicate public interface path");
    result[key] = row; count++; return row;
  }
  function builtinName(value) {
    if (builtins.has(value)) return builtins.get(value);
    if (identity(value) && typeof value !== "function") {
      const constructor = descriptor(value, "constructor");
      // A user-defined subclass prototype is NOT a core instance. Its own
      // methods remain public even when its parent is Transform.prototype.
      if (constructor && typeof constructor.value === "function" && constructor.value.prototype === value)
        return null;
      const proto = prototypeOf(value);
      if (proto && proto !== Object.prototype && builtins.has(proto))
        return "instance:" + builtins.get(proto);
    }
    return null;
  }
  function emit(path, value, depth, parent) {
    if (!room(parent, [encode(path)])) return;
    const row = {};
    if (identity(value) && visited.has(value)) {
      aliases++; put(path, {alias_of: visited.get(value)}); return;
    }
    put(path, row);
    row.kind = value === null ? "null" : typeof value;
    if (!identity(value)) return;
    visited.set(value, encode(path));
    const builtin = builtinName(value);
    if (builtin) {
      row.builtin = builtin;
      if (typeof value === "function") {
        row.length = row.arity = value.length;
        row.source_sha256 = sha(functionSource(value));
        row.formal_shape = {opaque_builtin: builtin, arity: value.length};
      }
      return;
    }
    if (depth > limits.max_depth) {
      truncate(row, "depth_limit", publicNames(value)); return;
    }
    if (typeof value === "function") {
      row.length = row.arity = value.length;
      Object.assign(row, callableInfo(value)); // Cached hashes/shapes, never source.
      if ("opaque_native_arity" in row.formal_shape) {
        row.builtin = "native:" + row.source_sha256; return;
      }
    }
    const names = publicNames(value).filter(name =>
      typeof value !== "function" || !["length", "name", "arguments", "caller"].includes(name) &&
      (name !== "prototype" || exposedClasses.has(value)));
    for (let index = 0; index < names.length; index++) {
      const name = names[index];
      if (!room(row, names.slice(index))) break;
      member(path.concat("own", name), descriptor(value, name), depth + 1, row);
    }
    const proto = prototypeOf(value);
    const parentClass = typeof value === "function" ? proto : descriptor(proto || {}, "constructor")?.value;
    const publicInheritance = (exposedClasses.has(value) || classPrototypes.has(value) ||
      typeof value === "object" && exposedClasses.has(parentClass)) &&
      (exposedClasses.has(parentClass) || builtins.has(proto));
    if (proto && publicInheritance && room(row, ["[[Prototype]]"]))
      emit(path.concat("[[Prototype]]"), proto, depth + 1, row);
  }
  function member(path, property, depth, parent) {
    if ("value" in property) emit(path, property.value, depth, parent);
    else {
      if (!room(parent, [encode(path)])) return;
      const row = put(path, {kind: "accessor", get: Boolean(property.get), set: Boolean(property.set)});
      if (property.get) emit(path.concat("get"), property.get, depth + 1, row);
      if (property.set) emit(path.concat("set"), property.set, depth + 1, row);
    }
  }
  const entries = config.entries.map(entry => ({...entry, mode: entry.mode || "import"})).sort((a, b) => {
    const left = a.path + "\0" + a.mode, right = b.path + "\0" + b.mode;
    return left < right ? -1 : left > right ? 1 : 0;
  });
  const modules = [];
  for (const entry of entries) {
    if (!["require", "import"].includes(entry.mode)) throw new Error("Invalid entry mode");
    const location = root + "/" + entry.path;
    const namespace = entry.mode === "require" ? require(location) : await import(pathToFileURL(location).href);
    const exports = typeof namespace === "function" ? [["module", namespace, null]] :
      publicNames(namespace).map(name =>
        ["exports", name, descriptor(namespace, name)]);
    const bindings = exports.map(([type, name, property]) => ({
      type, name, property, value: type === "module" ? namespace : namespace[name]}));
    modules.push({entry, namespace, bindings});
  }
  // Discover classes explicitly exposed through public export/member paths,
  // not implementation closures or non-exported prototype ancestors.
  const discovery = modules.flatMap(module => module.bindings.map(binding => [binding.value, 0]));
  const discovered = new WeakSet();
  for (let index = 0; index < discovery.length && index < limits.max_nodes; index++) {
    const [value, depth] = discovery[index];
    if (!identity(value) || discovered.has(value) || builtinName(value) || depth > limits.max_depth) continue;
    discovered.add(value);
    if (classLike(value)) {
      exposedClasses.add(value);
      const proto = descriptor(value, "prototype")?.value;
      if (proto) classPrototypes.add(proto);
    }
    for (const name of publicNames(value)) {
      if (["prototype", "constructor", "length", "name", "arguments", "caller"].includes(name)) continue;
      const property = descriptor(value, name);
      if ("value" in property && identity(property.value)) discovery.push([property.value, depth + 1]);
    }
  }
  for (const {entry, bindings} of modules) {
    for (let exportIndex = 0; exportIndex < bindings.length; exportIndex++) {
      if (!room(meta, bindings.slice(exportIndex).map(binding => String(binding.name)))) break;
      const {type, name, property, value} = bindings[exportIndex];
      const path = type === "module" ? [entry.path, entry.mode, "module"] : [entry.path, entry.mode, "exports", name];
      // Resolving module export getters preserves the original probe's import
      // semantics. General prototype/static/namespace accessors are never invoked.
      const row = put(path, {kind: "export_binding", value_kind: value === null ? "null" : typeof value,
                            get: Boolean(property?.get), set: Boolean(property?.set)});
      if (typeof value === "function") row.length = row.arity = value.length;
      emit(path.concat("value"), value, 0, row);
      if (property?.get) emit(path.concat("get"), property.get, 0, row);
      if (property?.set) emit(path.concat("set"), property.set, 0, row);
    }
  }
  meta.nodes = count; meta.aliases = aliases;
  const ordered = Object.fromEntries(Object.keys(result).sort().map(key => [key, result[key]]));
  fs.writeFileSync(process.argv[4] || "/scratch/interface.json", JSON.stringify(ordered));
})().catch(error => { console.error(error); process.exit(1); });
