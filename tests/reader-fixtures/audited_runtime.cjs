"use strict";

// A temporary synthetic export for packaging tests. Only the public extension
// bytes come from the checkout; no account or application data is read.
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const path = require("node:path");

function auditedRuntime(root, base) {
  const runtime = path.join(base, "runtime");
  const script = `
import hashlib,json,sys
from pathlib import Path
source,target=map(Path,sys.argv[1:])
sys.path.insert(0,str(source))
from tools import build_desktop,build_reader
files={name:b'Synthetic packaging fixture\\n' for name in build_desktop.REQUIRED}
files['VERSION']=b'1.2.3\\n'
files['release.json']=b'{"github_owner":"Example-Owner"}\\n'
files.update({'browser-reader/'+name:(source/'browser-reader'/name).read_bytes()
              for name in build_reader.SHIPPED})
target.mkdir()
for name,content in files.items():
    file=target/name
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_bytes(content)
manifest={'version':'1.2.3','files':{name:{'sha256':hashlib.sha256(content).hexdigest(),
                                       'bytes':len(content)} for name,content in sorted(files.items())}}
(target/'RELEASE-MANIFEST.json').write_text(json.dumps(manifest))
`;
  const result = childProcess.spawnSync("python3", ["-I", "-c", script, root, runtime], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr + result.stdout);
  return runtime;
}

module.exports = { auditedRuntime };
