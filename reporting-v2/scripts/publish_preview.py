#!/usr/bin/env python3
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path('/Users/rudolfkonfal/.openclaw/workspace/reporting-v2')
WORKTREE = Path(os.environ.get('PREVIEW_WORKTREE', '/Users/rudolfkonfal/.openclaw/workspace/tmp/diamond-plus-reporting-preview'))
REPO = os.environ.get('PREVIEW_REPO', 'rkonfal/diamond-plus-reporting-preview')
BRANCH = os.environ.get('PREVIEW_BRANCH', 'main')
SITE_DIR = ROOT / 'site'
CURRENT_DIR = ROOT / 'data' / 'current'
PREVIEWS_DIR = ROOT / 'previews'
PREVIEW_FILES = [
    'dashboard-portal-preview-clean-light.png',
    'dashboard-eshop-preview-clean-light.png',
]


def run(*args, cwd=None):
    print('+', ' '.join(args))
    subprocess.run(args, cwd=cwd, check=True)


def ensure_clone():
    if (WORKTREE / '.git').exists():
        run('git', 'fetch', 'origin', cwd=str(WORKTREE))
        run('git', 'checkout', BRANCH, cwd=str(WORKTREE))
        run('git', 'reset', '--hard', f'origin/{BRANCH}', cwd=str(WORKTREE))
        return
    if WORKTREE.exists():
        shutil.rmtree(WORKTREE)
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)
    try:
        run('gh', 'repo', 'clone', REPO, str(WORKTREE))
    except Exception:
        run('git', 'clone', f'https://github.com/{REPO}.git', str(WORKTREE))
        run('git', 'checkout', BRANCH, cwd=str(WORKTREE))


def clear_worktree():
    for child in WORKTREE.iterdir():
        if child.name == '.git':
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def build_dynamic_pages():
    run('python3', 'scripts/build_order_bump_report.py', cwd=str(ROOT))


def export_preview():
    build_dynamic_pages()
    clear_worktree()
    shutil.copytree(SITE_DIR, WORKTREE / 'site')
    shutil.copytree(CURRENT_DIR, WORKTREE / 'data' / 'current')

    audit_page = SITE_DIR / 'ads-audit-2026-04.html'
    if audit_page.exists():
        shutil.copy2(audit_page, WORKTREE / 'ads-audit-2026-04.html')

    target_previews = WORKTREE / 'previews'
    target_previews.mkdir(parents=True, exist_ok=True)
    for name in PREVIEW_FILES:
        src = PREVIEWS_DIR / name
        if src.exists():
            shutil.copy2(src, target_previews / name)

    (WORKTREE / '.nojekyll').write_text('', encoding='utf-8')
    (WORKTREE / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=site/index.html"><title>Diamond Plus Reporting Preview</title><p>Redirecting to <a href="site/index.html">site preview</a>…</p>',
        encoding='utf-8',
    )

    for page in SITE_DIR.glob('*.html'):
        if page.name == 'index.html':
            continue
        (WORKTREE / page.name).write_text(
            f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=site/{page.name}"><title>Diamond Plus Reporting Preview</title><p>Redirecting to <a href="site/{page.name}">{page.name}</a>…</p>',
            encoding='utf-8',
        )

    (WORKTREE / 'README.md').write_text(
        '# Diamond Plus Reporting Preview\n\n'
        'Static preview export of reporting-v2.\n\n'
        '- Main preview: `site/index.html`\n'
        '- Root page shortcuts: `/*.html` redirect to matching `site/*.html`\n'
        '- Current preview data: `data/current/`\n'
        '- Preview boards: `previews/`\n',
        encoding='utf-8',
    )


def commit_and_push():
    run('git', 'add', '-A', cwd=str(WORKTREE))
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=str(WORKTREE))
    if diff.returncode == 0:
        print('No preview changes to publish.')
        return
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run('git', 'commit', '-m', f'Publish preview {stamp}', cwd=str(WORKTREE))
    run('git', 'push', 'origin', BRANCH, cwd=str(WORKTREE))


def main():
    ensure_clone()
    export_preview()
    commit_and_push()
    print(f'Preview repo updated: https://github.com/{REPO}')
    owner, repo = REPO.split('/', 1)
    print(f'Preview site: https://{owner}.github.io/{repo}/')


if __name__ == '__main__':
    main()
