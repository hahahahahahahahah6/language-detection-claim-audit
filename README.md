# Static prose-to-source auditor

Version 0.1.0 checks documented promises against Python AST operations. It reads local files without executing project code, contacting services, or changing the inspected tree. Python 3.9 or newer is required. There are no runtime dependencies.

Install and run:

```sh
python3 -m pip install .
language-detection-claim-audit /path/to/project
python3 -m language_detection_claim_audit /path/to/project --json
python3 -m language_detection_claim_audit --version
```

The vocabulary contains 33 positive phrases and 20 negative phrases. The exact tuples are in the package's phrases.py file. Example prose and resulting rules:

```text
language detection
supports language identification
no language detection
does not detect language

documented-language-detection-claim-false: HIGH when production evidence is absent
Same rule: MEDIUM when evidence exists only in tests
documented-no-language-detection-claim-false: HIGH when production evidence exists
```

Each family produces at most one finding. A positive promise without any source files produces HIGH; a negative promise without any source files passes. With no matching prose, the result is PASS. A MEDIUM finding remains visible even when the default threshold permits exit 0.

The auditor reads root README.md and Markdown files with a docs directory component. Backtick and tilde fences with up to three leading spaces are excluded; inline code counts. Blank lines separate paragraphs. Matching lowercases text, expands contractions, replaces hyphens with spaces, and collapses whitespace. It uses exact phrases with word boundaries, longest first and alphabetical ties. Negative spans are removed before positive matching.

Source files are parsed with ast and never imported. Symlinked files and directories are skipped. The fixtures, docs, and examples directory components exclude source evidence. A tests component or a test_ prefix / _test.py suffix marks test evidence, counted separately. Syntax-invalid files contribute no evidence. Resolution stays within a single module, follows import aliases, and honors locally defined shadows.

Recognized operations (each route counts independently):

```text
langdetect.detect / detect_langs
langid.classify
cld3.get_language
pycld2.detect
guess_language.guess_language
lingua: detect_language_of in an importing module, including chained build calls
polyglot.detect.Detector construction
fasttext: predict on a load_model-bound object with a literal containing "lid"
          (case-insensitive; direct chained load_model(...).predict(...) counts)
spacy: add_pipe("language_detector") with a first positional string literal
hosted: attribute calls ending in .detect_language, excluding audio operations
```

Imports alone, configuration, locale formatting, translation, generic classifiers, sentiment/topic/NER pipelines, audio operations, strings, subprocess CLIs, unresolved calls, and local shadows do not supply evidence. Representative exclusions:

```text
langid.set_languages / norm / rank
langdetect.DetectorFactory.seed
gettext / babel / locale formatting and language-code lookup tables
translator.translate and transformers translation pipelines
sklearn classifiers and transformers text-classification pipelines
fasttext.load_model("topic.bin").predict(text)
whisper.detect_language and speechbrain audio models
```

These are static fingerprints, not proof that a model works at runtime. Model variables, cross-module data flow, dynamic imports, and dynamically computed model paths are outside the analysis.

CLI options:

| Option | Behavior |
| --- | --- |
| --json | One object with status, findings, claims, production_evidence, test_evidence |
| --verbose | Include claim and evidence details in text output |
| --ignore PATH | Skip a relative path prefix at a component boundary; repeatable |
| --fail-on SEVERITY | LOW, MEDIUM, HIGH (default), or CRITICAL |
| --version | Print 0.1.0 |

Each finding has rule, severity, and message fields. Status is PASS when no findings exist, otherwise FAIL. Exit 0 means no finding reached the selected threshold; exit 2 means at least one did. Usage and I/O errors exit 1.

```sh
python3 -m language_detection_claim_audit . --ignore build --ignore dist --verbose
python3 -m language_detection_claim_audit /path/to/project --fail-on MEDIUM --json
```

Neighboring audits address independent promises:

```text
translation-claim-audit: translation evidence, explicitly excluding language detection
A tool doing both operations is checked independently for each promise.
i18n-claim-audit: locale formatting; locale strings and code tables do not count here.
classification-claim-audit: image classifiers; generic tabular classifiers are excluded
there and generic fasttext predict without a lid literal is excluded here.
sentiment-claim-audit: sentiment pipelines rather than language identity.
voice-claim-audit: speech recognition rather than text language identity;
whisper detect_language is audio language identification and excluded here.
```

Development checks:

```sh
python3 -m unittest discover -s tests
python3 -m py_compile language_detection_claim_audit/__init__.py language_detection_claim_audit/__main__.py language_detection_claim_audit/audit.py language_detection_claim_audit/phrases.py
python3 -m build
python3 -m language_detection_claim_audit . --json
```

Tests compare the phrase tuples to fixed expected tuples. Licensed under MIT; see LICENSE.
