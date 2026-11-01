"""Read-only prose and AST audit; source is never imported or executed."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
import tokenize

from . import __version__
from .phrases import LANGDET, NOLANGDET

LEVELS = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3, 'CRITICAL': 4}


def normalize(text):
    return ' '.join(text.lower().replace("n't", ' not').replace('-', ' ').split())


def ordered_matches(text, phrases):
    """Return nonoverlapping matches, choosing longer phrases before shorter ones."""
    matches = []
    occupied = []
    for phrase in sorted(phrases, key=lambda p: (-len(p), p)):
        for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text):
            start, end = match.span()
            if not any(start < b and end > a for a, b in occupied):
                matches.append((phrase, start, end))
                occupied.append((start, end))
    return matches


def prose(text):
    lines = []
    fence = None
    for line in text.splitlines():
        if fence:
            if re.match(r'^ {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}\s*$', line):
                fence = None
            lines.append('')
            continue
        opening = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if opening and not (opening[1][0] == '`' and '`' in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            lines.append('')
        else:
            lines.append(line)
    return [normalize(p) for p in re.split(r'\n\s*\n', '\n'.join(lines)) if p.strip()]


def document_claims(text):
    claims = []
    for paragraph in prose(text):
        negative = ordered_matches(paragraph, NOLANGDET)
        remainder = list(paragraph)
        for phrase, start, end in negative:
            remainder[start:end] = ' ' * (end - start)
            claims.append({'family': 'NOLANGDET', 'phrase': phrase})
        for phrase, _, _ in ordered_matches(''.join(remainder), LANGDET):
            claims.append({'family': 'LANGDET', 'phrase': phrase})
    return claims


def source_evidence(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    aliases = {}
    definitions = set()
    assignments = {}
    lingua = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
                lingua |= item.name == 'lingua' or item.name.startswith('lingua.')
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if node.level:
                continue
            lingua |= module == 'lingua' or module.startswith('lingua.')
            for item in node.names:
                aliases[item.asname or item.name] = module + '.' + item.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.add(node.name)
        elif isinstance(node, ast.arg):
            definitions.add(node.arg)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name):
                        assignments.setdefault(child.id, []).append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            definitions.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
    # Assigning to an imported identifier shadows it, even if its value looks like an import.
    shadows = definitions | set(assignments)

    def resolve(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, '') if node.id not in shadows else ''
        if isinstance(node, ast.Attribute):
            base = resolve(node.value)
            return base + '.' + node.attr if base else ''
        return ''

    def load_is_lid(node):
        return (isinstance(node, ast.Call) and resolve(node.func) == 'fasttext.load_model'
                and any(isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and 'lid' in arg.value.lower()
                        for arg in list(node.args) + [k.value for k in node.keywords]))

    def lid_object(node):
        if load_is_lid(node):
            return True
        if isinstance(node, ast.Name) and node.id not in definitions:
            values = assignments.get(node.id, [])
            return len(values) == 1 and load_is_lid(values[0])
        return False

    def audio_object(node, seen=None):
        seen = set() if seen is None else seen
        name = resolve(node)
        if name.split('.')[0] in ('whisper', 'speechbrain', 'faster_whisper'):
            return True
        if isinstance(node, ast.Call):
            return audio_object(node.func, seen)
        if isinstance(node, ast.Attribute):
            return audio_object(node.value, seen)
        if isinstance(node, ast.Name) and node.id not in seen:
            seen.add(node.id)
            return any(audio_object(v, seen) for v in assignments.get(node.id, []))
        return False

    routes = {'langdetect.detect', 'langdetect.detect_langs', 'langid.classify',
              'cld3.get_language', 'pycld2.detect', 'guess_language.guess_language',
              'polyglot.detect.Detector'}
    evidence = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = resolve(node.func)
        route = resolved if resolved in routes else None
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr not in definitions:
            if func.attr == 'detect_language_of' and lingua:
                route = 'lingua.detect_language_of'
            elif func.attr == 'predict' and lid_object(func.value):
                route = 'fasttext.lid.predict'
            elif (func.attr == 'add_pipe' and node.args
                  and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'language_detector'):
                route = 'spacy.add_pipe'
            elif func.attr == 'detect_language' and not audio_object(func.value):
                # Hosted rule: any base counts. A locally defined
                # detect_language method is already excluded above,
                # because func.attr is then in definitions.
                route = 'hosted.detect_language'
        if route:
            evidence.append({'line': node.lineno, 'route': route})
    return evidence


def audit(root, ignore=()):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise OSError('root must be a readable directory')
    if (not root.stat().st_mode & 0o444 or not root.stat().st_mode & 0o111
            or not os.access(root, os.R_OK | os.X_OK)):
        raise OSError('root must be a readable directory')
    prefixes = [Path(p).as_posix().removeprefix('./').rstrip('/') for p in ignore]

    def ignored(relative):
        name = relative.as_posix()
        return any(not p or name == p or name.startswith(p + '/') for p in prefixes)

    claims, production, tests = [], [], []
    def walk_error(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if not (base / d).is_symlink() and not ignored((base / d).relative_to(root)))
        for filename in sorted(files):
            path = base / filename
            relative = path.relative_to(root)
            if path.is_symlink() or ignored(relative):
                continue
            parts = relative.parts
            if (relative == Path('README.md') or ('docs' in parts[:-1] and path.suffix == '.md')):
                for claim in document_claims(path.read_text(encoding='utf-8')):
                    claims.append(dict(claim, path=relative.as_posix()))
            if path.suffix != '.py':
                continue
            if any(p in ('fixtures', 'docs', 'examples') for p in parts[:-1]):
                continue
            try:
                with tokenize.open(str(path)) as stream:
                    source = stream.read()
            except (SyntaxError, UnicodeError, LookupError):
                continue
            target = tests if ('tests' in parts[:-1] or filename.startswith('test_') or filename.endswith('_test.py')) else production
            for evidence in source_evidence(source):
                target.append(dict(evidence, path=relative.as_posix()))
    findings = []
    families = {c['family'] for c in claims}
    if 'LANGDET' in families and not production:
        severity = 'MEDIUM' if tests else 'HIGH'
        message = 'Documented promise has evidence only in tests.' if tests else 'Documented promise has no production evidence.'
        findings.append({'rule': 'documented-language-detection-claim-false', 'severity': severity, 'message': message})
    if 'NOLANGDET' in families and production:
        findings.append({'rule': 'documented-no-language-detection-claim-false', 'severity': 'HIGH',
                         'message': 'Documented denial conflicts with production evidence.'})
    return {'status': 'FAIL' if findings else 'PASS', 'findings': findings, 'claims': claims,
            'production_evidence': production, 'test_evidence': tests}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, '%s: error: %s\n' % (self.prog, message))


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('root', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[])
    parser.add_argument('--fail-on', choices=LEVELS, default='HIGH')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ignore)
    except (OSError, UnicodeError, SyntaxError) as error:
        print('error: %s' % error, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result['status'])
        for finding in result['findings']:
            print('{severity} {rule}: {message}'.format(**finding))
        if args.verbose:
            for key in ('claims', 'production_evidence', 'test_evidence'):
                print('%s: %s' % (key, json.dumps(result[key], sort_keys=True)))
    return 2 if any(LEVELS[f['severity']] >= LEVELS[args.fail_on] for f in result['findings']) else 0
