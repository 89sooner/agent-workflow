"""Build local distribution archives; never publish or edit agent settings."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def package(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'agent-workflow-marketplace-0.1.0.zip'
    files = [ROOT/'.claude-plugin/marketplace.json', ROOT/'.agents/plugins/marketplace.json']
    files += [p for p in (ROOT/'plugins').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):
            z.write(p, p.relative_to(ROOT))
    npm = output / 'agent-workflow-observer-0.1.0.tgz'
    with tarfile.open(npm, 'w:gz') as tar:
        for name in ['package.json', 'index.js', 'bridge.js', 'v2.js', 'v2/index.js', 'README.md']:
            tar.add(ROOT/'plugins/opencode'/name, arcname='package/'+name)
    receipt = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (archive,npm)}
    (output/'SHA256SUMS.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'output': str(output), 'sha256': receipt}, indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'dist/plugins'))
    package(parser.parse_args().output)
