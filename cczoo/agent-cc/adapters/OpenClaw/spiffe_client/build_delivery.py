#!/usr/bin/env python3
"""Build a source + Linux amd64 client delivery with a complete checksum manifest."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import build_plugin

ROOT = Path(__file__).resolve().parent
AGENT_CC = ROOT.parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--upstream', type=Path, required=True, help='integrity-pinned npm upstream tgz')
    p.add_argument('--spire-archive', type=Path, help='optional official SPIRE 1.15.3 Linux amd64 musl archive')
    p.add_argument('--output', type=Path, default=ROOT/'dist/openclaw-ip1-tdvm-stage2.tar.gz')
    a = p.parse_args()
    dist = ROOT/'dist'
    dist.mkdir(exist_ok=True)
    plugin, receipt = build_plugin.build(a.upstream.read_bytes())
    plugin_name = 'openviking-openclaw-plugin-2026.6.18-argus.2'
    (dist/(plugin_name+'.tgz')).write_bytes(plugin)
    (dist/(plugin_name+'.json')).write_bytes((json.dumps(receipt,indent=2)+'\n').encode())
    binary = dist/'spiffe-client-credentials'
    env = dict(os.environ, GOOS='linux', GOARCH='amd64', CGO_ENABLED='0')
    subprocess.run(['go','build','-mod=readonly','-trimpath','-buildvcs=false','-o',str(binary),
                    './cmd/spiffe-client-credentials'],cwd=AGENT_CC/'core/spire/helpers/spiffe-helper',env=env,check=True)
    if binary.read_bytes()[:4] != b'\x7fELF':
        raise ValueError('expected a Linux ELF binary')
    repo=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=ROOT,text=True).strip())
    paths=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=repo).decode().split('\0')
    prefixes=['adapters/OpenClaw/spiffe_client/','core/spire/helpers/spiffe-helper/','core/spire/workload/','core/spire/tests/tdvm/']
    scripts={'openclaw_tdvm.sh','openclaw_spiffe_common.sh','connect_openclaw_openviking.sh',
             'verify_openclaw_plugin_e2e.sh','observe_openclaw_lifecycle.sh'}
    documents={'Argus-IP1-OpenClaw-IP2-OpenViking-Implementation-Plan-CN.md'}
    files={}
    for name in paths:
        if not name: continue
        file=repo/name
        if not file.is_file(): continue
        try: rel=file.relative_to(AGENT_CC).as_posix()
        except ValueError: continue
        if (any(rel.startswith(v) for v in prefixes)
                or (rel.startswith('adapters/OpenClaw/scripts/') and file.name in scripts)
                or (rel.startswith('documents_ly/') and file.name in documents)
                or rel in ('LICENSE','NOTICE')):
            data=file.read_bytes()
            # Windows checkouts may use CRLF; Linux shell delivery must use LF.
            files['agent-cc/'+rel]=data.replace(b'\r\n',b'\n') if file.suffix=='.sh' else data
    for file in (binary,dist/(plugin_name+'.tgz'),dist/(plugin_name+'.json')):
        files['agent-cc/'+file.relative_to(AGENT_CC).as_posix()]=file.read_bytes()
    if a.spire_archive:
        data=a.spire_archive.read_bytes()
        if hashlib.sha256(data).hexdigest()!='ca1a4d1155317bdd2afc7f36663828a10410c7c840e54725b90b4064b0a301c7':
            raise ValueError('official SPIRE archive digest mismatch')
        files['vendor/spire-1.15.3-linux-amd64-musl.tar.gz']=data
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}
    manifest={'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
              'source_worktree_modified':bool(subprocess.check_output(['git','status','--porcelain'],cwd=repo)),
              'go_version':subprocess.check_output(['go','version'],text=True).strip(),
              'plugin':receipt,'helper_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
              'company_execution':'NOT_RUN','openclaw_tdx_remote_attestation':'NOT_RUN','files':hashes}
    files['MANIFEST.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    files['SHA256SUMS']=''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name,data in sorted(files.items())).encode()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('wb') as output, gzip.GzipFile(fileobj=output,mode='wb',filename='',mtime=0) as gz, tarfile.open(fileobj=gz,mode='w') as archive:
        for name,data in sorted(files.items()):
            item=tarfile.TarInfo(name)
            item.size=len(data)
            item.mode=0o755 if name.endswith('.sh') or name.endswith('/spiffe-client-credentials') else 0o644
            archive.addfile(item,io.BytesIO(data))
    digest=hashlib.sha256(a.output.read_bytes()).hexdigest()
    a.output.with_suffix(a.output.suffix+'.sha256').write_bytes((digest+'  '+a.output.name+'\n').encode())
    print(json.dumps({'artifact':str(a.output.resolve()),'sha256':digest,'files':len(files),'helper_sha256':manifest['helper_sha256']}))


if __name__=='__main__': main()
