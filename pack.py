"""Package frontend/dist + backend into hospital-chat-0.1.0.supx.

Stand-in for `superset-extensions build/bundle`, whose npm lookup fails on
Windows. Run `npm run build` in frontend/ first, then `python pack.py`.
"""
import glob
import json
import os
import zipfile

ext = json.load(open("extension.json", encoding="utf8"))
dist = sorted(glob.glob("frontend/dist/*.js"))
remote = [os.path.basename(f) for f in dist if "remoteEntry" in os.path.basename(f)]
assert len(remote) == 1, f"expected one remoteEntry in frontend/dist, got {remote}"

manifest = {
    "publisher": ext["publisher"],
    "name": ext["name"],
    "displayName": ext["displayName"],
    "version": ext["version"],
    "dependencies": [],
    "permissions": ext.get("permissions", []),
    "id": f"{ext['publisher']}.{ext['name']}",
    "frontend": {
        "remoteEntry": remote[0],
        "moduleFederationName": "demo_hospitalChat",
    },
    "backend": {"entrypoint": "demo.hospital_chat.entrypoint"},
}

out = f"{ext['name']}-{ext['version']}.supx"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("manifest.json", json.dumps(manifest, indent=2))
    for f in dist:
        z.write(f, "frontend/dist/" + os.path.basename(f))
    for f in glob.glob("backend/src/**/*.py", recursive=True):
        z.write(f, f.replace(os.sep, "/"))
print("wrote", out)
