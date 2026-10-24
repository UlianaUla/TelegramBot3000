"""Build a source-only archive with an explicit allowlist, never include .env/data."""
from pathlib import Path
import hashlib
import json
import re
import zipfile

ROOT=Path(__file__).resolve().parent


def main():
    paths=[]
    for folder in ['bot','tests']:
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.html','.cjs'}:
                paths.append(p)
    for p in ROOT.iterdir():
        if p.is_file() and (p.suffix=='.md' or p.name in {
            'requirements.txt','.env.example','.gitignore','start-miniapp.cmd','prepare_submission.py'}):
            paths.append(p)
    manifest=[]
    for p in sorted(paths):
        relative=p.relative_to(ROOT).as_posix()
        if p.name=='.env' or any(x in p.parts for x in ['data','.venv','venv','.git']):
            raise ValueError('Forbidden path in submission')
        text=p.read_text(encoding='utf-8-sig')
        if re.search(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}',text):
            raise ValueError('Possible bot token in '+relative)
        keys=re.findall(r'\b(?:test|live)_[A-Za-z0-9_-]{24,}',text)
        if any(any(c.isdigit() for c in key) and any(c.isupper() for c in key) for key in keys):
            raise ValueError('Possible provider key in '+relative)
        manifest.append({'path':relative,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    example=dict(line.split('=',1) for line in (ROOT/'.env.example').read_text(encoding='utf-8-sig').splitlines()
                 if '=' in line and not line.lstrip().startswith('#'))
    if any(example.get(k,'').strip() for k in ['BOT_TOKEN','YOOKASSA_SHOP_ID','YOOKASSA_SECRET_KEY','PROVIDER_TOKEN']):
        raise ValueError('Secret fields in .env.example must be empty')
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    target=out/'foliant-stock-source.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(paths):
            archive.write(p,p.relative_to(ROOT).as_posix())
        archive.writestr('SUBMISSION_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    print('Source archive:',target)
    print('Files:',len(manifest),'; .env, database, binaries and virtualenv excluded')


if __name__=='__main__':
    main()
