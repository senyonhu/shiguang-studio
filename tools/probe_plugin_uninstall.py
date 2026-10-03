"""Real CLI uninstall scope audit using only newly minted no-MCP fixtures."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
import tomllib
import uuid


ROOT = Path(__file__).resolve().parents[1]

def discover_cli():
    found = shutil.which('codex.exe') or shutil.which('codex')
    if found:
        return Path(found)
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidates = list((Path(local) / 'OpenAI/Codex/bin').glob('*/codex.exe'))
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return None


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def tree(path):
    path = Path(path).resolve()
    result = {}
    if not path.exists():
        return result
    for current, dirs, names in os.walk(path, followlinks=False):
        for name in list(dirs) + names:
            entry = Path(current) / name
            info = entry.lstat()
            if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                # Existing cache/latest junctions are legitimate CLI-managed
                # identities. Do not traverse them or mutate their targets.
                result[entry.relative_to(path).as_posix()] = {'kind': 'link', 'target_sha256': digest(os.readlink(entry))}
                if name in dirs:
                    dirs.remove(name)
            elif entry.is_file():
                result[entry.relative_to(path).as_posix()] = {'sha256': sha(entry), 'bytes': info.st_size}
    return dict(sorted(result.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, default=discover_cli())
    args = parser.parse_args()
    if args.cli is None:
        parser.error('Native Codex CLI was not found; provide --cli explicitly.')
    cli = args.cli.resolve()
    run = ROOT / 'evidence/m7-plugin-uninstall/runs' / str(time.time_ns())
    run.mkdir(parents=True, exist_ok=False)
    suffix = uuid.uuid4().hex[:16]
    marketplace = 'rb-uninstall-fixture-' + suffix
    target, sentinel = 'rb-target-' + suffix, 'rb-sentinel-' + suffix
    target_id, sentinel_id = target + '@' + marketplace, sentinel + '@' + marketplace
    fixture_ids = {target_id, sentinel_id}
    codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()
    config = codex_home / 'config.toml'
    bridge_home = (Path(os.environ['LOCALAPPDATA']) / 'RenPyBridge').resolve()
    source = run / 'marketplace'
    fixture_root = source / '.agents/plugins/marketplace.json'
    entries = []
    for name in (target, sentinel):
        write(source / name / '.codex-plugin/plugin.json', {'name': name, 'version': '0.0.1', 'description': 'Owned uninstall scope fixture; no MCP or executable code.', 'skills': './skills/'})
        skill = source / name / 'skills/fixture/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: fixture\ndescription: Inert owned uninstall scope fixture.\n---\nThis fixture has no MCP server, tools, hooks or executable code.\n', encoding='utf-8')
        entries.append({'name': name, 'source': {'source': 'local', 'path': './' + name}, 'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}})
    write(fixture_root, {'name': marketplace, 'plugins': entries})
    report = {'complete': False, 'scope': 'official CLI uninstall of newly minted inert fixtures in current registry; formal Bridge is never removed',
              'cli': {'path': str(cli), 'sha256': sha(cli)}, 'run_directory': str(run),
              'fixture': {'marketplace': marketplace, 'target': target_id, 'sentinel': sentinel_id, 'source': str(source), 'no_mcp': True},
              'environment_overrides': {}, 'commands': [], 'snapshots': {}, 'checks': {}, 'cleanup': {}}
    report['snapshot_scope'] = 'All regular content files plus existing link target identity hashes; existing cache/latest junctions are never followed.'
    report_path = run / 'report.json'
    created_marketplace = False
    installed = set()

    def command(label, argv):
        before = time.monotonic()
        result = subprocess.run([str(cli), *argv], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        value = json.loads(result.stdout) if '--json' in argv and result.returncode == 0 else None
        # Never persist account config or arbitrary stderr; exact byte digests
        # bind the native output and selected non-secret structure is retained.
        entry = {'label': label, 'argv': [str(cli), *argv], 'cwd': str(ROOT), 'exit_code': result.returncode,
                 'duration_seconds': time.monotonic() - before,
                 'stdout_sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
                 'stderr_sha256': hashlib.sha256(result.stderr.encode()).hexdigest()}
        if label == 'version':
            report['cli']['version'] = result.stdout.strip()
        elif value is not None:
            entry['structured_output_sha256'] = digest(value)
            if argv[:2] == ['plugin', 'list']:
                entry['listing'] = [{key: item.get(key) for key in ('pluginId', 'name', 'marketplaceName', 'version', 'installed', 'enabled')} for item in value.get('installed', [])]
            elif argv[:3] == ['plugin', 'marketplace', 'list']:
                entry['listing'] = [{'name': item.get('name'), 'root_sha256': digest(item.get('root'))} for item in value.get('marketplaces', [])]
            else:
                entry['fixture_result'] = {key: val for key, val in value.items() if key in {'pluginId', 'name', 'marketplaceName', 'installed', 'enabled', 'version', 'removed'}}
        report['commands'].append(entry)
        write(report_path, report)
        print(label, result.returncode, flush=True)
        if result.returncode:
            raise RuntimeError('CLI failed: ' + label + '; output digests recorded without secret values')
        return value

    def config_snapshot():
        value = tomllib.loads(config.read_text(encoding='utf-8'))
        protected = deepcopy(value)
        for key in fixture_ids:
            protected.get('plugins', {}).pop(key, None)
        protected.get('marketplaces', {}).pop(marketplace, None)
        return {'semantic_sha256': digest(value), 'protected_semantic_sha256': digest(protected),
                'file_sha256': sha(config), 'fixture_plugin_entries': sorted(set(value.get('plugins', {})) & fixture_ids),
                'fixture_marketplace_entry': marketplace in value.get('marketplaces', {})}

    def snapshot(label):
        plugins = command(label + '-plugins', ['plugin', 'list', '--json'])
        markets = command(label + '-marketplaces', ['plugin', 'marketplace', 'list', '--json'])
        members = {item['pluginId']: digest(item) for item in plugins.get('installed', [])}
        listing = {'plugins': members, 'marketplaces': {item['name']: digest(item) for item in markets.get('marketplaces', [])}, 'config': config_snapshot()}
        report['snapshots'][label] = listing
        write(run / (label + '-listing.json'), listing)
        write(report_path, report)
        return listing

    try:
        command('version', ['--version'])
        baseline = snapshot('baseline')
        assert fixture_ids.isdisjoint(baseline['plugins']) and marketplace not in baseline['marketplaces']
        protected_cache_before = tree(codex_home / 'plugins/cache')
        project_before = tree(bridge_home / 'projects')
        write(run / 'protected-cache-before.json', {'files': protected_cache_before, 'tree_sha256': digest(protected_cache_before)})
        write(run / 'formal-projects-before.json', {'files': project_before, 'tree_sha256': digest(project_before)})
        command('add-marketplace', ['plugin', 'marketplace', 'add', str(source), '--json'])
        created_marketplace = True
        for name in (target_id, sentinel_id):
            command('add-target' if name == target_id else 'add-sentinel', ['plugin', 'add', name, '--json'])
            installed.add(name)
        both = snapshot('both-installed')
        assert fixture_ids <= set(both['plugins'])
        command('remove-target', ['plugin', 'remove', target_id, '--json'])
        installed.remove(target_id)
        after = snapshot('after-target-removal')
        report['checks']['target_absent'] = target_id not in after['plugins']
        report['checks']['sentinel_unchanged'] = sentinel_id in after['plugins'] and after['plugins'][sentinel_id] == both['plugins'][sentinel_id]
        report['checks']['every_existing_plugin_unchanged'] = all(after['plugins'].get(key) == value for key, value in baseline['plugins'].items())
        report['checks']['no_unexpected_plugin_identity'] = set(after['plugins']) == set(baseline['plugins']) | {sentinel_id}
        report['checks']['formal_bridge_still_installed'] = 'renpy-bridge@renpy-bridge-personal' in after['plugins']
        report['checks']['existing_config_semantics_unchanged'] = after['config']['protected_semantic_sha256'] == baseline['config']['protected_semantic_sha256']
        report['checks']['existing_marketplaces_unchanged'] = all(after['marketplaces'].get(key) == value for key, value in baseline['marketplaces'].items())
        # Ignore only this run's two unique cache identities. Compare every
        # pre-existing cached file exactly, and reject unexplained additions.
        now_cache = tree(codex_home / 'plugins/cache')
        protected_after = {key:value for key,value in now_cache.items() if marketplace not in Path(key).parts and target not in Path(key).parts and sentinel not in Path(key).parts}
        report['checks']['existing_plugin_cache_bytes_unchanged'] = protected_after == protected_cache_before
        project_after = tree(bridge_home / 'projects')
        report['checks']['all_formal_project_bytes_unchanged'] = project_after == project_before
        write(run / 'protected-cache-after-target.json', {'files': protected_after, 'tree_sha256': digest(protected_after)})
        write(run / 'formal-projects-after-target.json', {'files': project_after, 'tree_sha256': digest(project_after)})
        report['checks']['all_scope_checks_passed'] = all(report['checks'].values())
        write(report_path, report)
    except Exception as error:
        report['error'] = repr(error)
    finally:
        # Only names minted and installed by this run can enter cleanup argv.
        cleanup_errors = []
        for name in sorted(installed):
            try:
                command('cleanup-' + ('sentinel' if name == sentinel_id else 'target'), ['plugin', 'remove', name, '--json'])
            except Exception as error:
                cleanup_errors.append(repr(error))
        if created_marketplace:
            try:
                command('cleanup-marketplace', ['plugin', 'marketplace', 'remove', marketplace, '--json'])
            except Exception as error:
                cleanup_errors.append(repr(error))
        try:
            final = snapshot('final')
            final_projects = tree(bridge_home / 'projects')
            final_cache = tree(codex_home / 'plugins/cache')
            write(run / 'formal-projects-final.json', {'files': final_projects, 'tree_sha256': digest(final_projects)})
            write(run / 'protected-cache-final.json', {'files': final_cache, 'tree_sha256': digest(final_cache)})
            report['cleanup'] = {'errors': cleanup_errors, 'fixtures_absent': fixture_ids.isdisjoint(final['plugins']),
                                 'marketplace_absent': marketplace not in final['marketplaces'],
                                 'existing_plugin_listing_restored': final['plugins'] == baseline['plugins'],
                                 'existing_marketplaces_restored': final['marketplaces'] == baseline['marketplaces'],
                                 'existing_config_semantics_restored': final['config']['protected_semantic_sha256'] == baseline['config']['protected_semantic_sha256'],
                                 'full_config_semantics_restored': final['config']['semantic_sha256'] == baseline['config']['semantic_sha256'],
                                 'cache_bytes_restored': final_cache == protected_cache_before,
                                 'all_formal_project_bytes_unchanged': final_projects == project_before}
        except Exception as error:
            report['cleanup']['error'] = repr(error)
        report['complete'] = report['checks'].get('all_scope_checks_passed', False) and not report['cleanup'].get('errors', [True]) and all(value is True for key,value in report['cleanup'].items() if key != 'errors')
        write(report_path, report)
        print(json.dumps({'report': str(report_path), 'sha256': sha(report_path), 'complete': report['complete'], 'checks': report['checks'], 'cleanup': report['cleanup']}, ensure_ascii=True), flush=True)
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
