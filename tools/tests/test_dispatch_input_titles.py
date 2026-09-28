"""Dispatch inputs are fully and unambiguously visible in workflow run titles."""
from __future__ import annotations

import itertools
import re
import sys
import unittest
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import gen_windows_arm64_workflow as arm_generator


TARGETS = {
    'windows-x64': {
        'path': ROOT / '.github/workflows/build-win-x64-github.yml',
        'prefix': 'wx64',
        'inputs': ('build_profile', 'compile_jobs', 'use_upstream_cache', 'upstream_run_id',
                   'resume_run_id', 'resume_stage', 'resume_tree_stage', 'resume_attempt',
                   'resume_source_sha', 'resume_artifact_ids'),
    },
    'windows-arm64': {
        'path': ROOT / '.github/workflows/build-win-arm64-github.yml',
        'prefix': 'warm',
        'inputs': ('build_profile', 'compile_jobs', 'use_upstream_cache', 'upstream_run_id'),
    },
    'linux-x64': {
        'path': ROOT / '.github/workflows/build-linux-x64.yml',
        'prefix': 'lx64',
        'inputs': ('build_profile', 'compile_jobs', 'build_mode', 'use_upstream_cache',
                   'resume_run_id', 'resume_tree_stage', 'resume_attempt', 'resume_artifact_ids'),
    },
    'linux-arm64': {
        'path': ROOT / '.github/workflows/build-linux-arm64.yml',
        'prefix': 'larm',
        'inputs': ('build_profile', 'compile_jobs', 'build_mode', 'use_upstream_cache',
                   'resume_run_id', 'resume_tree_stage', 'resume_attempt', 'resume_artifact_ids'),
    },
}


def events(workflow):
    return workflow.get('on', workflow.get(True))


def split_arguments(source):
    arguments = []
    start = 0
    depth = 0
    quote = None
    for index, char in enumerate(source):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            arguments.append(source[start:index].strip())
            start = index + 1
    arguments.append(source[start:].strip())
    return arguments


def parse_run_name(expression):
    expression = expression.strip()
    prefix = "${{ github.event_name == 'workflow_dispatch' && "
    if not expression.startswith(prefix) or not expression.endswith(' }}'):
        raise AssertionError(f'unexpected run-name expression: {expression}')
    body = expression[len(prefix):-3]
    fallback_separator = " || '"
    format_start = "format('"
    if not body.startswith(format_start):
        raise AssertionError(f'run-name is not format-based: {expression}')
    template_start = len(format_start)
    template_end = body.index("', ", template_start)
    template = body[template_start:template_end]
    format_body_start = template_end + 3
    depth = 1
    quote = None
    close = None
    for index in range(format_body_start, len(body)):
        char = body[index]
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                close = index
                break
    if close is None:
        raise AssertionError(f'unclosed format expression: {expression}')
    arguments = split_arguments(body[format_body_start:close])
    remainder = body[close + 1:]
    if not remainder.startswith(fallback_separator) or not remainder.endswith("'"):
        raise AssertionError(f'run-name has an unexpected fallback: {expression}')
    fallback = remainder[len(fallback_separator):-1]
    return template, arguments, fallback


def input_name(argument):
    match = re.fullmatch(r'inputs\.([a-z_]+)', argument)
    if match:
        return match.group(1)
    match = re.fullmatch(r"format\('\{0\}', inputs\.([a-z_]+)\)", argument)
    if match:
        return match.group(1)
    raise AssertionError(f'run-name argument is not a direct input: {argument}')


def evaluate(expression, values, event='workflow_dispatch'):
    template, arguments, fallback = parse_run_name(expression)
    if event != 'workflow_dispatch':
        return fallback
    rendered = []
    for argument in arguments:
        name = input_name(argument)
        value = values[name]
        if isinstance(value, bool):
            value = str(value).lower()
        rendered.append(str(value))
    return template.format(*rendered)


def schema_for(target):
    workflow = yaml.safe_load(target['path'].read_text(encoding='utf-8'))
    return workflow, events(workflow)['workflow_dispatch']['inputs']


def defaults_for(schema):
    return {name: definition.get('default') for name, definition in schema.items()}


def normalized_values(schema, values):
    return {name: values.get(name, definition.get('default'))
            for name, definition in schema.items()}


def authorized_values(schema, values):
    if set(values) != set(schema):
        return False
    for name, definition in schema.items():
        value = values[name]
        kind = definition['type']
        if kind == 'boolean' and not isinstance(value, bool):
            return False
        if kind == 'choice' and value not in definition['options']:
            return False
        if kind == 'string' and not isinstance(value, str):
            return False
        if isinstance(value, str) and (len(value.encode()) > 200 or any(char in value for char in '|\r\n')):
            return False
    return True


def alternate_value(name, definition, current):
    if definition['type'] == 'boolean':
        return not current
    if definition['type'] == 'choice':
        return next(option for option in definition['options'] if option != current)
    values = {
        'compile_jobs': '16',
        'resume_attempt': '2',
        'resume_run_id': '36144376832',
        'resume_stage': '5',
        'resume_tree_stage': '8',
        'resume_source_sha': 'f' * 40,
        'resume_artifact_ids': '10902309838,10915484727',
        'upstream_run_id': '36093095228',
    }
    return values.get(name, 'alternate')


class DispatchInputTitlesTest(unittest.TestCase):
    def authorized_titles(self, workflow, schema):
        value_options = []
        for name, definition in schema.items():
            if definition['type'] == 'boolean':
                options = [False, True]
            elif definition['type'] == 'choice':
                options = definition['options']
            else:
                options = [definition.get('default'), alternate_value(name, definition,
                                                                      definition.get('default'))]
            value_options.append(options)
        return {
            evaluate(workflow['run-name'], dict(zip(schema, values)))
            for values in itertools.product(*value_options)
        }

    def test_exact_input_sets_and_generated_arm_workflow(self):
        self.assertEqual(arm_generator.OUTPUT.read_text(encoding='utf-8'), arm_generator.render())
        for target in TARGETS.values():
            workflow, schema = schema_for(target)
            self.assertEqual(set(schema), set(target['inputs']))
            self.assertEqual(len(schema), len(target['inputs']))
            self.assertIn('run-name', workflow)
            self.assertNotIn('toJSON', workflow['run-name'])
            self.assertNotIn('hash', workflow['run-name'])
            if target['prefix'] == 'warm':
                self.assertEqual(workflow['run-name'], arm_generator.workflow()['run-name'])

    def test_expression_uses_every_real_input_in_fixed_order(self):
        for target in TARGETS.values():
            workflow, schema = schema_for(target)
            template, arguments, fallback = parse_run_name(workflow['run-name'])
            self.assertEqual(fallback, workflow['name'])
            self.assertEqual(tuple(input_name(argument) for argument in arguments), target['inputs'])
            self.assertEqual(template.split()[0], target['prefix'])
            self.assertIn("format('{0}', inputs.use_upstream_cache)", arguments)
            self.assertNotIn('toJSON', workflow['run-name'])
            self.assertNotIn('github.event.inputs', workflow['run-name'])
            self.assertLessEqual(len(evaluate(workflow['run-name'], defaults_for(schema)).encode()), 200)

    def test_each_input_mutation_changes_title(self):
        for target in TARGETS.values():
            workflow, schema = schema_for(target)
            base = defaults_for(schema)
            original = evaluate(workflow['run-name'], base)
            for name, definition in schema.items():
                changed = deepcopy(base)
                changed[name] = alternate_value(name, definition, changed[name])
                with self.subTest(target=target['prefix'], input=name):
                    self.assertNotEqual(evaluate(workflow['run-name'], changed), original)

    def test_defaults_and_real_dispatch_values_have_expected_titles(self):
        expected = {
            'wx64': 'wx64 profile=native jobs=auto cache=true upstream= run= stage=4 tree= attempt=1 source= artifacts=',
            'warm': 'warm profile=native jobs=auto cache=false upstream=',
            'lx64': 'lx64 profile=fast jobs=auto mode=staged cache=true run= tree=7 attempt=1 artifacts=',
            'larm': 'larm profile=fast jobs=auto mode=staged cache=true run= tree=7 attempt=1 artifacts=',
        }
        task_values = {
            'wx64': {'build_profile': 'native', 'compile_jobs': 'auto', 'use_upstream_cache': True,
                     'upstream_run_id': '36093095228'},
            'lx64': {'build_profile': 'release', 'compile_jobs': 'auto', 'build_mode': 'staged',
                     'use_upstream_cache': True},
            'larm': {'build_profile': 'release', 'compile_jobs': 'auto', 'build_mode': 'staged',
                     'use_upstream_cache': True},
        }
        task_expected = {
            'wx64': 'wx64 profile=native jobs=auto cache=true upstream=36093095228 run= stage=4 tree= attempt=1 source= artifacts=',
            'lx64': 'lx64 profile=release jobs=auto mode=staged cache=true run= tree=7 attempt=1 artifacts=',
            'larm': 'larm profile=release jobs=auto mode=staged cache=true run= tree=7 attempt=1 artifacts=',
        }
        for target in TARGETS.values():
            workflow, schema = schema_for(target)
            values = defaults_for(schema)
            with self.subTest(target=target['prefix']):
                self.assertEqual(evaluate(workflow['run-name'], values), expected[target['prefix']])
                self.assertTrue(authorized_values(schema, values))
                if target['prefix'] in task_values:
                    values.update(task_values[target['prefix']])
                    self.assertTrue(authorized_values(schema, values))
                    self.assertEqual(evaluate(workflow['run-name'], values), task_expected[target['prefix']])
                    self.assertLessEqual(len(evaluate(workflow['run-name'], values).encode()), 200)

    def test_missing_wrong_type_delimiter_and_overlong_values_are_not_authorized_titles(self):
        for target in TARGETS.values():
            workflow, schema = schema_for(target)
            base = defaults_for(schema)
            authorized = self.authorized_titles(workflow, schema)
            for name, definition in schema.items():
                missing = dict(base)
                del missing[name]
                normalized = normalized_values(schema, missing)
                with self.subTest(target=target['prefix'], input=name, case='missing'):
                    self.assertEqual(evaluate(workflow['run-name'], normalized),
                                     evaluate(workflow['run-name'], base))

                wrong_type = dict(base)
                wrong_type[name] = False if definition['type'] != 'boolean' else 'false'
                with self.subTest(target=target['prefix'], input=name, case='wrong-type'):
                    self.assertFalse(authorized_values(schema, wrong_type))

                if definition['type'] != 'boolean':
                    for case, value in (('delimiter', 'x|y'), ('overlong', 'x' * 201)):
                        malformed = dict(base)
                        malformed[name] = value
                        with self.subTest(target=target['prefix'], input=name, case=case):
                            self.assertFalse(authorized_values(schema, malformed))
                            self.assertNotIn(evaluate(workflow['run-name'], malformed), authorized)

    def test_non_dispatch_uses_original_workflow_name(self):
        for target in TARGETS.values():
            workflow, _ = schema_for(target)
            self.assertEqual(evaluate(workflow['run-name'], {}, event='push'), workflow['name'])


if __name__ == '__main__':
    unittest.main()
