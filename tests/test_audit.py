"""Behavioral tests and phrase oracle checks."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from language_detection_claim_audit.audit import audit, document_claims, main, normalize, ordered_matches, source_evidence
from language_detection_claim_audit.phrases import LANGDET, NOLANGDET



class PhraseTests(unittest.TestCase):
    def test_lists_match_spec(self):
        # Fixed oracle tuples, transcribed from the round spec and
        # verified against it by the coordinator before shipping;
        # the spec file itself is intentionally not shipped.
        expected_langdet = (
        'language detection',
        'detects language',
        'detect language',
        'detects the language',
        'language identification',
        'identifies language',
        'identify language',
        'identifies the language',
        'language detector',
        'detects languages',
        'determines language',
        'determine language',
        'determines the language',
        'automatically detects language',
        'automatic language detection',
        'auto language detection',
        'detects input language',
        'identifies input language',
        'detects written language',
        'identifies written language',
        'text language detection',
        'detects text language',
        'language detection support',
        'supports language detection',
        'support language detection',
        'language identification support',
        'supports language identification',
        'detects what language',
        'identifies the input language',
        'detects the input language',
        'language detection capabilities',
        'language identification capabilities',
        'detects the written language',
    )
        expected_nolangdet = (
        'no language detection',
        'does not detect language',
        'does not detect the language',
        'without language detection',
        'no language identification',
        'does not identify language',
        'without language identification',
        'does not determine language',
        'cannot detect language',
        'never detects language',
        'no language detector',
        'does not support language detection',
        'no language detection support',
        'without language detector',
        'does not identify the language',
        'cannot identify language',
        'never identifies language',
        'no automatic language detection',
        'does not automatically detect language',
        'language detection not supported',
    )
        self.assertEqual(len(expected_langdet), 33)
        self.assertEqual(len(expected_nolangdet), 20)
        self.assertEqual(LANGDET, expected_langdet)
        self.assertEqual(NOLANGDET, expected_nolangdet)

    def test_normalization(self):
        self.assertEqual(normalize("DOESN'T detect-language\n  here"), 'does not detect language here')
        self.assertEqual(document_claims("doesn't detect-language")[0]['family'], 'NOLANGDET')

    def test_longest_first(self):
        self.assertEqual(ordered_matches('supports language detection and language detection', LANGDET),
                         [('supports language detection', 0, 27), ('language detection', 32, 50)])

    def test_alphabetical_tie(self):
        self.assertEqual([x[0] for x in ordered_matches('zzz aaa', ('zzz', 'aaa'))], ['aaa', 'zzz'])

    def test_negative_precedence(self):
        for phrase in NOLANGDET:
            with self.subTest(phrase=phrase):
                self.assertEqual(document_claims(phrase), [{'family': 'NOLANGDET', 'phrase': phrase}])

    def test_boundaries(self):
        self.assertEqual(document_claims('prelanguage detectionish'), [])

    def test_paragraph_boundaries(self):
        self.assertEqual(document_claims('language\n\ndetection'), [])
        self.assertEqual(document_claims('language\ndetection')[0]['family'], 'LANGDET')

    def test_inline_code_counts(self):
        self.assertEqual(document_claims('`language detection`')[0]['family'], 'LANGDET')

    def test_fences(self):
        for marker in ('```', '~~~', '````', '~~~~'):
            for indent in range(4):
                with self.subTest(marker=marker, indent=indent):
                    self.assertEqual(document_claims(' ' * indent + marker + 'text\nlanguage detection\n' + ' ' * indent + marker), [])
        self.assertTrue(document_claims('    ```\nlanguage detection\n    ```'))

    def test_fence_closing(self):
        self.assertEqual(document_claims('````\n```\nlanguage detection\n````'), [])
        self.assertEqual(document_claims('~~~\n```\nlanguage detection\n~~~'), [])
        self.assertEqual(document_claims('```\nlanguage detection'), [])


class EvidenceTests(unittest.TestCase):
    pass


def evidence_test(source, expected):
    def test(self):
        self.assertEqual(bool(source_evidence(source)), expected, source)
    return test


ROUTES = {
    'langdetect_detect': ('langdetect', 'detect'),
    'langdetect_detect_langs': ('langdetect', 'detect_langs'),
    'langid': ('langid', 'classify'),
    'cld3': ('cld3', 'get_language'),
    'pycld2': ('pycld2', 'detect'),
    'guess_language': ('guess_language', 'guess_language'),
    'polyglot': ('polyglot.detect', 'Detector'),
}
for label, (module, function) in ROUTES.items():
    forms = {
        'attribute': 'import {m}\n{m}.{f}(text)',
        'alias': 'import {m} as engine\nengine.{f}(text)',
        'from': 'from {m} import {f}\n{f}(text)',
        'from_alias': 'from {m} import {f} as operation\noperation(text)',
    }
    for form, template in forms.items():
        setattr(EvidenceTests, 'test_route_' + label + '_' + form,
                evidence_test(template.format(m=module, f=function), True))
    setattr(EvidenceTests, 'test_shadow_' + label,
            evidence_test('from {m} import {f}\ndef {f}(text): pass\n{f}(text)'.format(m=module, f=function), False))
    setattr(EvidenceTests, 'test_assignment_shadow_' + label,
            evidence_test('import {m} as engine\nengine = object()\nengine.{f}(text)'.format(m=module, f=function), False))

POSITIVE = {
    'lingua': 'import lingua\ndetector.detect_language_of(text)',
    'lingua_from': 'from lingua import LanguageDetectorBuilder\ndetector.detect_language_of(text)',
    'lingua_submodule': 'import lingua.builder as builder\ndetector.detect_language_of(text)',
    'lingua_chained': 'from lingua import LanguageDetectorBuilder as B\nB.from_all_languages().build().detect_language_of(text)',
    'lingua_bound_build': 'import lingua\ndetector = lingua.LanguageDetectorBuilder.from_all_languages().build()\ndetector.detect_language_of(text)',
    'polyglot_from_module': 'from polyglot import detect as pd\npd.Detector(text)',
    'fasttext': 'import fasttext\nmodel = fasttext.load_model("lid.176.bin")\nmodel.predict(text)',
    'fasttext_alias': 'import fasttext as ft\nmodel = ft.load_model("LID.ftz")\nmodel.predict(text)',
    'fasttext_from': 'from fasttext import load_model\nmodel = load_model("lid.bin")\nmodel.predict(text)',
    'fasttext_from_alias': 'from fasttext import load_model as load\nmodel = load("lid.bin")\nmodel.predict(text)',
    'fasttext_chained': 'import fasttext\nfasttext.load_model("lid.bin").predict(text)',
    'fasttext_chained_alias': 'from fasttext import load_model as load\nload("LID.bin").predict(text)',
    'fasttext_keyword': 'import fasttext\nfasttext.load_model(path="LID.bin").predict(text)',
    'spacy': 'nlp.add_pipe("language_detector")',
    'spacy_chained': 'create_nlp().add_pipe("language_detector")',
    'hosted': 'client.detect_language(Text=text)',
    'hosted_chain': 'client.service.detect_language(text)',
    'hosted_constructed': 'make_client().detect_language(text)',
    'hosted_arg': 'def check(client):\n    return client.detect_language(text)',
    'hosted_arg_chain': 'def check(svc):\n    return svc.client.detect_language(text)',
    'inside_function': 'from langdetect import detect\ndef run(text):\n    return detect(text)',
}
NEGATIVE = {
    'imports': 'import langdetect, langid, cld3, pycld2, guess_language, lingua, polyglot.detect, fasttext, spacy',
    'langid_config': 'import langid\nlangid.set_languages(["en"])\nlangid.norm(text)\nlangid.rank(text)',
    'factory': 'from langdetect import DetectorFactory\nDetectorFactory.seed = 0',
    'wrong_detector': 'from other import Detector\nDetector(text)',
    'locale': 'import locale\nlocale.setlocale(locale.LC_ALL, "en_US")',
    'gettext': 'import gettext\ngettext.gettext(text)',
    'babel': 'from babel.numbers import format_decimal\nformat_decimal(123, locale="en")',
    'lookup': 'languages = {"en": "English"}\nresult = languages["en"]',
    'translation': 'translator.translate(text)',
    'translation_pipeline': 'from transformers import pipeline\npipeline("translation")(text)',
    'generic_fasttext': 'import fasttext\nm = fasttext.load_model("model.bin")\nm.predict(text)',
    'generic_fasttext_chained': 'import fasttext\nfasttext.load_model("topic.bin").predict(text)',
    'generic_fasttext_variable': 'import fasttext\npath = "lid.bin"\nm = fasttext.load_model(path)\nm.predict(text)',
    'generic_fasttext_unresolved': 'm.predict(text)',
    'sklearn': 'from sklearn.linear_model import LogisticRegression\nLogisticRegression().predict(text)',
    'classifier_pipeline': 'from transformers import pipeline\npipeline("text-classification")(text)',
    'sentiment_pipeline': 'from transformers import pipeline\npipeline("sentiment-analysis")(text)',
    'topic_pipeline': 'from transformers import pipeline\npipeline("zero-shot-classification")(text)',
    'ner_pipeline': 'from transformers import pipeline\npipeline("ner")(text)',
    'whisper': 'import whisper\nwhisper.detect_language(audio)',
    'whisper_alias': 'import whisper as w\nw.detect_language(audio)',
    'whisper_from': 'from whisper import detect_language\ndetect_language(audio)',
    'whisper_model': 'import whisper\nmodel = whisper.load_model("base")\nmodel.detect_language(audio)',
    'speechbrain': 'from speechbrain.inference.classifiers import EncoderClassifier\nmodel = EncoderClassifier.from_hparams(source="lang-id")\nmodel.classify_batch(audio)',
    'strings': 'text = "langdetect.detect language detector en"',
    'subprocess': 'import subprocess\nsubprocess.run(["langdetect", text])',
    'bare_reference': 'from langdetect import detect\noperation = detect',
    'unresolved': 'detect(text)\nclassify(text)\nget_language(text)',
    'unresolved_attribute': 'unknown.detect(text)\nunknown.classify(text)\nunknown.get_language(text)',
    'invalid': 'from langdetect import detect\ndetect(text)\nthis is invalid !!!',
    'lingua_shadow': 'import lingua\nclass Local:\n    def detect_language_of(self, text): pass\nLocal().detect_language_of(text)',
    'lingua_no_import': 'detector.detect_language_of(text)',
    'fasttext_shadow': 'from fasttext import load_model\ndef load_model(path): pass\nload_model("lid.bin").predict(text)',
    'hosted_shadow': 'class Client:\n    def detect_language(self, text): pass\nclient.detect_language(text)',
    'spacy_shadow': 'class Local:\n    def add_pipe(self, name): pass\nobj.add_pipe("language_detector")',
    'predict_shadow': 'import fasttext\nclass Local:\n    def predict(self, text): pass\nfasttext.load_model("lid.bin").predict(text)',
    'spacy_wrong_keyword': 'nlp.add_pipe(name="language_detector")',
    'spacy_wrong_literal': 'nlp.add_pipe("other")',
    'relative_import': 'from .langdetect import detect\ndetect(text)',
}
for label, source in POSITIVE.items():
    setattr(EvidenceTests, 'test_positive_' + label, evidence_test(source, True))
for label, source in NEGATIVE.items():
    setattr(EvidenceTests, 'test_negative_' + label, evidence_test(source, False))
for family, phrases in [('positive', LANGDET), ('negative', NOLANGDET)]:
    for index, phrase in enumerate(phrases):
        def phrase_test(self, phrase=phrase, family=family):
            expected = 'LANGDET' if family == 'positive' else 'NOLANGDET'
            self.assertEqual(document_claims(phrase), [{'family': expected, 'phrase': phrase}])
        setattr(PhraseTests, 'test_exhaustion_%s_%02d' % (family, index), phrase_test)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding='utf-8')
        return path

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main([str(self.root), *args])
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def claim(self):
        self.put('README.md', 'language detection')

    def evidence(self, name='app.py'):
        return self.put(name, 'from langdetect import detect\ndetect("hello")')

    def test_good(self):
        self.claim()
        self.evidence()
        self.assertEqual(audit(self.root)['status'], 'PASS')
        self.assertEqual(self.cli()[0], 0)

    def test_bad(self):
        self.claim()
        self.put('app.py', 'import langdetect')
        result = audit(self.root)
        self.assertEqual(result['findings'][0]['rule'], 'documented-language-detection-claim-false')
        self.assertEqual(result['findings'][0]['severity'], 'HIGH')
        self.assertEqual(self.cli()[0], 2)

    def test_no_source_positive(self):
        self.claim()
        self.assertEqual(self.cli()[0], 2)

    def test_no_source_negative(self):
        self.put('README.md', 'no language detection')
        self.assertEqual(audit(self.root)['status'], 'PASS')
        self.assertEqual(self.cli()[0], 0)

    def test_no_claim(self):
        self.evidence()
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_negative_high(self):
        self.put('README.md', 'no language detection')
        self.evidence()
        finding = audit(self.root)['findings'][0]
        self.assertEqual(finding['rule'], 'documented-no-language-detection-claim-false')
        self.assertEqual(finding['severity'], 'HIGH')
        self.assertEqual(self.cli()[0], 2)

    def test_medium(self):
        self.claim()
        self.evidence('tests/check.py')
        result = audit(self.root)
        self.assertEqual(result['findings'][0]['severity'], 'MEDIUM')
        self.assertEqual(result['production_evidence'], [])
        self.assertEqual(len(result['test_evidence']), 1)
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual(self.cli('--fail-on', 'MEDIUM')[0], 2)

    def test_test_names(self):
        self.claim()
        for name in ('test_app.py', 'app_test.py', 'nested/tests/check.py'):
            self.evidence(name)
        result = audit(self.root)
        self.assertEqual(len(result['test_evidence']), 3)
        self.assertEqual(result['production_evidence'], [])

    def test_excluded_components(self):
        self.claim()
        for directory in ('fixtures', 'docs', 'examples'):
            self.evidence('nested/' + directory + '/app.py')
            self.evidence(directory + '/tests/test_app.py')
        result = audit(self.root)
        self.assertEqual(result['production_evidence'], [])
        self.assertEqual(result['test_evidence'], [])
        self.assertEqual(result['findings'][0]['severity'], 'HIGH')

    def test_syntax_invalid(self):
        self.claim()
        self.put('app.py', 'import langdetect\nlangdetect.detect(text)\nbroken !!!')
        self.assertEqual(audit(self.root)['production_evidence'], [])

    def test_single_module(self):
        self.claim()
        self.put('imports.py', 'from langdetect import detect')
        self.put('app.py', 'detect(text)')
        self.assertEqual(audit(self.root)['production_evidence'], [])

    def test_docs_selection(self):
        self.put('other.md', 'language detection')
        self.put('nested/README.md', 'language detection')
        self.assertEqual(audit(self.root)['claims'], [])
        self.put('nested/docs/guide.md', 'language detection')
        self.assertEqual(len(audit(self.root)['claims']), 1)

    def test_one_per_family(self):
        self.put('README.md', 'language detection\n\ndetects language\n\nno language detection')
        self.put('docs/guide.md', 'language detection')
        self.assertEqual(len(audit(self.root)['findings']), 1)
        self.evidence()
        self.assertEqual(len(audit(self.root)['findings']), 1)

    def test_ignore_repeatable(self):
        self.claim()
        self.evidence('src/app.py')
        self.evidence('other/app.py')
        self.assertEqual(self.cli('--ignore', 'src', '--ignore', 'other')[0], 2)
        self.assertEqual(self.cli('--ignore', 'README.md')[0], 0)

    def test_ignore_component_prefix(self):
        self.claim()
        self.evidence('source/app.py')
        self.assertEqual(self.cli('--ignore', 'src')[0], 0)

    def test_json(self):
        self.claim()
        code, stdout, _ = self.cli('--json')
        self.assertEqual(code, 2)
        result = json.loads(stdout)
        self.assertEqual(set(result), {'status', 'findings', 'claims', 'production_evidence', 'test_evidence'})
        self.assertEqual(set(result['findings'][0]), {'rule', 'severity', 'message'})
        self.assertEqual(result['status'], 'FAIL')

    def test_verbose(self):
        self.assertIn('production_evidence:', self.cli('--verbose')[1])

    def test_fail_on(self):
        self.claim()
        self.assertEqual(self.cli('--fail-on', 'CRITICAL')[0], 0)
        self.assertEqual(self.cli('--fail-on', 'LOW')[0], 2)
        self.assertEqual(self.cli('--fail-on', 'invalid')[0], 1)

    def test_version(self):
        code, output, _ = self.cli('--version')
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), '0.1.0')

    def test_bad_path(self):
        code, _, _ = self.cli(str(self.root / 'absent'))
        self.assertEqual(code, 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.root / 'absent')]), 1)
        self.put('file', '')
        with self.assertRaises(OSError):
            audit(self.root / 'file')

    def test_usage_error(self):
        self.assertEqual(self.cli('--unknown')[0], 1)

    def test_unreadable_root(self):
        self.root.chmod(0)
        try:
            self.assertEqual(self.cli()[0], 1)
        finally:
            self.root.chmod(0o700)

    def test_symlinks(self):
        self.claim()
        external = self.root / 'external'
        external.mkdir()
        self.evidence('external/app.py')
        (self.root / 'linked.py').symlink_to(external / 'app.py')
        (self.root / 'linked-dir').symlink_to(external, target_is_directory=True)
        (self.root / 'linked-doc.md').symlink_to(self.root / 'README.md')
        result = audit(self.root, ['external'])
        self.assertEqual(result['production_evidence'], [])
        self.assertEqual(len(result['claims']), 1)

    def test_invalid_source_encoding(self):
        self.claim()
        (self.root / 'app.py').write_bytes(b'# coding: unknown-codec\nimport langdetect\nlangdetect.detect(text)')
        self.assertEqual(audit(self.root)['production_evidence'], [])
        self.assertEqual(self.cli()[0], 2)

    def test_negative_test_only_pass(self):
        self.put('README.md', 'no language detection')
        self.evidence('test_app.py')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_symlink_document_excluded(self):
        original = self.put('claim.txt', 'language detection')
        (self.root / 'README.md').symlink_to(original)
        (self.root / 'docs').mkdir()
        (self.root / 'docs/claim.md').symlink_to(original)
        self.assertEqual(audit(self.root)['claims'], [])

    def test_no_execution(self):
        self.claim()
        self.put('app.py', 'raise RuntimeError("never execute")\nimport langdetect\nlangdetect.detect(text)')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_module_entrypoint(self):
        result = subprocess.run([sys.executable, '-m', 'language_detection_claim_audit', str(self.root), '--json'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
