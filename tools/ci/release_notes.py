#!/usr/bin/env python3
"""生成发行说明 / 变更日志。

数据源（唯一真源）：`manifest` 的 `changelog` 字段（飞牛应用中心展示的同一份文案）。
`changelog` 用全角竖线 `｜` 分隔各版本条目，每个条目以版本号开头，如：

    changelog = 1.1.14 ① … ② … ③ … ｜1.1.13 … ｜1.1.12 …

用法：
    python tools/ci/release_notes.py --tag v1.1.14 --out release-notes.md   # 生成该 tag 的发行说明
    python tools/ci/release_notes.py --changelog CHANGELOG.md               # 依据 manifest 重建 CHANGELOG.md
    python tools/ci/release_notes.py --tag v1.1.14 --check-only             # 只校验 tag 与 manifest 版本一致
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, 'manifest')
SEP = '｜'
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'


def read_manifest_field(name: str) -> str:
    with open(MANIFEST, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    m = re.search(r'^%s\s*=\s*(.*)$' % re.escape(name), text, re.M)
    return m.group(1).strip() if m else ''


def manifest_version() -> str:
    v = read_manifest_field('version')
    if not v:
        sys.exit('manifest 中找不到 version 字段')
    return v


def changelog_entries() -> list[tuple[str, str]]:
    """把 manifest.changelog 拆成 [(版本号, 文案), ...]，保持文件顺序（新版本在前）。"""
    raw = read_manifest_field('changelog')
    entries: list[tuple[str, str]] = []
    for chunk in raw.split(SEP):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r'^(\d+\.\d+\.\d+)\s*(.*)$', chunk, re.S)
        if not m:
            continue
        entries.append((m.group(1), m.group(2).strip()))
    return entries


def entries_for(version: str) -> list[str]:
    hits = [t for (v, t) in changelog_entries() if v == version]
    if not hits:
        sys.exit('manifest.changelog 中没有 %s 的条目' % version)
    return hits


def bulletize(text: str) -> list[str]:
    """带 ①②③ 标记的文案拆成条目；否则整段作为一个条目。"""
    if any(c in text for c in CIRCLED):
        parts = re.split(r'(?=[%s])' % re.escape(CIRCLED), text)
        return [p.strip() for p in parts if p.strip()]
    return [text.strip()]


def git(*args: str) -> str:
    exe = shutil.which('git') or os.environ.get('GIT_EXE') or 'git'
    try:
        out = subprocess.run([exe, *args], cwd=ROOT, capture_output=True, timeout=120)
    except (FileNotFoundError, subprocess.SubprocessError):
        return ''
    if out.returncode != 0:
        return ''
    return out.stdout.decode('utf-8', 'replace').strip()


def previous_tag(tag: str) -> str:
    tags = [t for t in git('tag', '--sort=-v:refname').split('\n') if t.startswith('v')]
    later = False
    for t in tags:
        if later:
            return t
        if t == tag:
            later = True
    return ''


def commit_lines(tag: str) -> list[str]:
    base = previous_tag(tag)
    rng = f'{base}..{tag}' if base else tag
    lines = git('log', '--pretty=%s', rng).split('\n')
    if not lines or lines == ['']:
        rng = f'{base}..HEAD' if base else 'HEAD'
        lines = git('log', '--pretty=%s', rng).split('\n')
    return [re.sub(r'^\w+\(v?[\d.]+\):\s*', '', l.strip()) for l in lines if l.strip()]


def diffstat(tag: str) -> str:
    base = previous_tag(tag)
    if not base:
        return ''
    return git('diff', '--shortstat', f'{base}..{tag}').strip()


def build_notes(version: str, tag: str, repo: str) -> str:
    out: list[str] = ['## 本版更新 · %s' % tag, '']
    for chunk in entries_for(version):
        for b in bulletize(chunk):
            out.append('- %s' % b)
    out.append('')

    commits = commit_lines(tag)
    if commits:
        out += ['<details><summary>提交记录（%d）</summary>' % len(commits), '']
        out += ['- %s' % c for c in commits]
        out += ['', '</details>', '']

    stat = diffstat(tag)
    if stat:
        out += ['## 变更范围', '', '- 自上一版本：%s' % stat, '']

    out += [
        '---',
        '',
        '📦 **安装 / 升级**：下载下方 `%s-qqmusic-downloader.fpk`，在飞牛 **应用中心 → 手动安装 → 选择该文件** 即可'
        '（保留数据升级，无需先卸载）。' % version,
        '',
        '🧩 **源码全包**：`%s-qqmusic-downloader-src.tar.gz`（该版本完整源码快照，便于审计与自行构建）。' % version,
        '',
        '📖 **完整功能清单**：[README](https://github.com/%s#功能)　·　'
        '🧾 **历史版本**：[CHANGELOG](https://github.com/%s/blob/main/CHANGELOG.md)' % (repo, repo),
    ]
    return '\n'.join(out)


def build_changelog(repo: str, version: str) -> str:
    out = [
        '# 更新日志',
        '',
        '> 本文件由 `tools/ci/release_notes.py` 依据 `manifest` 的 `changelog` 字段生成，'
        '**请勿手工编辑**；每个版本的发行说明与安装包见 [Releases](https://github.com/%s/releases)。' % repo,
        '',
        '当前版本：**v%s**' % version,
        '',
    ]
    grouped: list[tuple[str, list[str]]] = []
    for v, text in changelog_entries():
        if grouped and grouped[-1][0] == v:
            grouped[-1][1].append(text)
        else:
            grouped.append((v, [text]))
    for v, texts in grouped:
        out += ['## v%s' % v, '']
        for text in texts:
            for b in bulletize(text):
                out.append('- %s' % b)
        out.append('')
    return '\n'.join(out)


def sync_readme(path: str, version: str) -> None:
    """把 README 里的「当前版本 vX.Y.Z」改成 manifest 的版本号（保留原有加粗写法）。"""
    with open(path, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    pattern = r'(当前版本\s*\*{0,2}v)(\d+\.\d+\.\d+)(\*{0,2})'
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        sys.exit('README 中「当前版本 vX.Y.Z」标记应恰好出现 1 次，实际 %d 次' % len(matches))
    text = re.sub(pattern, lambda m: m.group(1) + version + m.group(3), text)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    print('已同步 %s 的版本号为 v%s' % (path, version))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--readme', default='')
    ap.add_argument('--changelog', default='')
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--print-version', action='store_true')
    ap.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', 'Tianxiaodudou/qqmcxzjm'))
    args = ap.parse_args()

    version = manifest_version()
    if args.print_version:
        print(version)
        return 0
    tag = args.tag or ('v' + version)
    if tag.lstrip('v') != version:
        sys.exit('tag %s 与 manifest 版本 %s 不一致：请先更新 manifest 的 version/changelog' % (tag, version))
    if args.check_only:
        print('版本一致：%s' % version)
        return 0

    if args.changelog:
        path = args.changelog if os.path.isabs(args.changelog) else os.path.join(ROOT, args.changelog)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(build_changelog(args.repo, version))
        print('已写入 %s' % path)

    if args.readme:
        sync_readme(args.readme if os.path.isabs(args.readme) else os.path.join(ROOT, args.readme), version)

    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(build_notes(version, tag, args.repo))
        print('已写入 %s' % path)

    if not args.out and not args.changelog:
        print(build_notes(version, tag, args.repo))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
