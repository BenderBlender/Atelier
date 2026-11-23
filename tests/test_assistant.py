import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sketchpad import assistant_backend as backend


class AssistantTests(unittest.TestCase):
    def test_ten_distinct_local_presets_with_provenance(self):
        presets = backend.load_presets()
        self.assertEqual(len(presets), 10)
        for preset in presets:
            self.assertGreater(len(preset['prompt']), 350)
            self.assertTrue(preset['reference_artists'][0]['source_url'].startswith('https://'))
            self.assertIn(backend.BASE_BRIEF, backend.build_prompt(preset, ''))

    def test_custom_prompt_and_limits(self):
        self.assertEqual('Светлый карандаш на чёрной бумаге', backend.build_prompt(None, 'Светлый карандаш на чёрной бумаге'))
        for bad in ('', ' ' * 5, 'x' * 8001):
            with self.assertRaises(backend.AssistantError):
                backend.build_prompt(None, bad)

    def test_subscription_never_inherits_api_credentials_or_parent_thread(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'CODEX_API_KEY': 'other',
                                     'CODEX_THREAD_ID': 'parent', 'CODEX_HOME': 'retained'}):
            env = backend.subscription_env()
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('CODEX_API_KEY', env)
        self.assertNotIn('CODEX_THREAD_ID', env)
        self.assertEqual(env['CODEX_HOME'], 'retained')

    def test_auth_does_not_treat_api_key_login_as_subscription(self):
        with patch.object(backend, 'discover_codex', return_value=Path('codex.exe')):
            for code, response, expected in [(0, 'Logged in using ChatGPT', True),
                                              (0, 'Logged in using an API key: SECRET', False),
                                              (1, 'Not logged in', False)]:
                with patch.object(backend, 'run_codex', return_value=(code, response, [])):
                    status = backend.subscription_status(backend.Cancellation())
                    self.assertEqual(status['ready'], expected)
                    self.assertNotIn('SECRET', json.dumps(status))

    def test_bridge_is_isolated_read_only_and_attaches_input(self):
        args = backend.generation_args(Path('isolated'), Path('source.png'))
        self.assertIn('--ignore-user-config', args)
        self.assertEqual(args[args.index('--sandbox')+1], 'read-only')
        self.assertEqual(args[args.index('--image')+1], 'source.png')
        for feature in ('shell_tool', 'apps', 'plugins', 'multi_agent'):
            self.assertIn(['--disable', feature], [args[i:i+2] for i in range(len(args)-1)])

    def test_output_lookup_only_accepts_returned_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            thread = '12345678-1234-1234-1234-123456789abc'
            directory = root/'generated_images'/thread
            directory.mkdir(parents=True)
            expected = directory/'result.png'
            expected.write_bytes(b'fixture')
            self.assertEqual(backend.generated_file([{'type':'thread.started', 'thread_id':thread}], root), expected.resolve())
            for invalid in ('../elsewhere', '1234', ''):
                with self.assertRaises(backend.AssistantError):
                    backend.generated_file([{'type':'thread.started', 'thread_id':invalid}], root)

    def test_api_edit_includes_source_and_optional_refined_prompt_no_key_on_disk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.png'
            source.write_bytes(b'input fixture')
            request = backend.Request(source, root, 'original brief', 'api', True, 'SECRET-KEY')
            responses = [ {'output': [{'type':'message', 'content':[{'type':'output_text', 'text':'refined graphite brief'}]}]},
                          {'data': [{'b64_json': base64.b64encode(b'output fixture').decode()}]} ]
            with patch.object(backend, 'api_post', side_effect=responses) as post:
                result = backend.generate(request, backend.Cancellation())
            self.assertEqual(post.call_count, 2)
            edit = post.call_args_list[1].args
            self.assertEqual(edit[0], '/v1/images/edits')
            self.assertIn('refined graphite brief', edit[1]['prompt'])
            self.assertTrue(edit[1]['images'][0]['image_url'].startswith('data:image/png;base64,'))
            self.assertEqual(Path(result['path']).read_bytes(), b'output fixture')
            self.assertNotIn('SECRET-KEY', (root/'result.json').read_text())
            self.assertNotIn('SECRET-KEY', repr(request))

    def test_api_error_never_retries_or_falls_back_to_subscription(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.png'
            source.write_bytes(b'fixture')
            request = backend.Request(source, root, 'brief', 'api', key='secret')
            with patch.object(backend, 'api_post', side_effect=backend.AssistantError('quota')) as post, patch.object(backend, 'run_codex') as codex:
                with self.assertRaises(backend.AssistantError):
                    backend.generate(request, backend.Cancellation())
            self.assertEqual(post.call_count, 1)
            codex.assert_not_called()
            self.assertFalse((root/'result.json').exists())

    def test_cancelled_request_does_not_start_provider(self):
        cancel = backend.Cancellation()
        cancel.cancel()
        with patch.object(backend, 'subscription_status') as status:
            with self.assertRaises(backend.Cancelled):
                backend.generate(backend.Request(Path('input'), Path('output'), 'brief'), cancel)
            status.assert_not_called()

    def test_subscription_uses_tool_and_copies_only_its_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.png'
            source.write_bytes(b'source')
            output = root/'generated.png'
            output.write_bytes(b'image fixture')
            request = backend.Request(source, root/'job', 'brief', enhance=True)
            with patch.object(backend, 'subscription_status', return_value={'ready':True, 'path':'codex.exe'}), patch.object(backend, 'run_codex', return_value=(0, '', [{'type':'turn.completed'}])) as run, patch.object(backend, 'generated_file', return_value=output):
                result = backend.generate(request, backend.Cancellation())
            self.assertIn('refine the visual brief', run.call_args.kwargs['prompt'])
            self.assertEqual(Path(result['path']).read_bytes(), b'image fixture')


if __name__ == '__main__':
    unittest.main()
