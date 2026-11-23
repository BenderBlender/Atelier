import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sketchpad import assistant_backend as backend
from sketchpad import agent_backend as agent
from sketchpad import higgsfield_backend as hf

JOB = '12345678-1234-1234-1234-123456789abc'


class HiggsfieldTests(unittest.TestCase):
    def test_default_model_and_native_auto(self):
        model = hf.model_info(hf.DEFAULT_MODEL)
        self.assertEqual(model['name'], 'Seedream 5.5')
        params = hf.parameters(hf.DEFAULT_MODEL, 'auto', {'resolution': '2k', 'background': 'auto'}, 1400, 1000)
        self.assertEqual(params, {'aspect_ratio': 'auto', 'resolution': '2k', 'background': 'auto'})

    def test_nearest_ratio_and_invalid_settings(self):
        self.assertEqual(hf.parameters('flux_2', 'auto', {}, 1400, 1000)['aspect_ratio'], '4:3')
        for model, aspect, values in [('seedream_5_5', 'auto', {'resolution': '4k'}),
                                      ('seedream_5_5', '2:1', {}), ('unknown', 'auto', {}),
                                      ('seedream_5_5', 'auto', {'prompt': 'hidden'})]:
            with self.assertRaises(backend.AssistantError): hf.parameters(model, aspect, values, 1400, 1000)

    def test_live_verified_cli_alias(self):
        self.assertEqual(hf.CLI_IDS.get('nano_banana_pro', 'nano_banana_pro'), 'nano_banana_pro')
        self.assertEqual(hf.CLI_IDS['nano_banana_2'], 'nano_banana_flash')

    def test_job_receipts_and_terminal_states(self):
        for value in ({'jobs': [{'id': JOB}]}, {'job_ids': [JOB]}, {'id': JOB}, [{'id': JOB}], {'data': {'job_id': JOB}}):
            self.assertEqual(hf.job_ids(value), [JOB])
        self.assertEqual(hf.job_ids({'job_set_id': JOB}), [])
        self.assertIsNone(hf.output_url({'status': 'queued', 'result_url': 'https://example.com/not-ready'}))
        self.assertEqual(hf.output_url({'status': 'completed', 'result_url': 'https://example.com/result.png'}), 'https://example.com/result.png')
        with self.assertRaises(backend.AssistantError): hf.output_url({'status': 'failed'})

    def test_generate_submits_once_and_preserves_literal_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source.png'; source.write_bytes(b'fixture')
            prompt = '  Белый карандаш, без желтизны.\nСохрани пропорции!  '
            request = backend.Request(source, root, prompt, provider='higgsfield', provider_options={
                'model': 'seedream_5_5', 'aspect': 'auto', 'parameters': {'resolution': '2k'},
                'source_size': [1400, 1000], 'workspace': JOB})
            schema = {'params': [{'name': 'aspect_ratio', 'enum': ['auto']}, {'name': 'resolution', 'enum': ['2k']}]}
            with patch.object(hf, 'select_workspace'), patch.object(hf, 'run', side_effect=[schema, {'jobs': [{'id': JOB}]}]) as run, \
                    patch.object(hf, 'retrieve', return_value={'path': 'fixture.png'}) as retrieve:
                result = backend.generate(request, backend.Cancellation())
            commands = [call.args[0] for call in run.call_args_list]
            submits = [a for a in commands if a[:2] == ['generate', 'create']]
            self.assertEqual(len(submits), 1)
            args = submits[0]
            self.assertEqual(args[args.index('--prompt')+1], prompt)
            self.assertEqual(args[args.index('--image')+1], source)
            self.assertEqual(args[args.index('--aspect_ratio')+1], 'auto')
            self.assertEqual(json.loads((root/'higgsfield-job.json').read_text())['job_ids'], [JOB])
            self.assertEqual(result['path'], 'fixture.png'); retrieve.assert_called_once()

    def test_changed_schema_never_submits(self):
        request = backend.Request(Path('unused'), Path('unused'), 'paint', provider='higgsfield',
                                  provider_options={'workspace': JOB, 'parameters': {'resolution': '2k'}})
        with patch.object(hf, 'select_workspace'), patch.object(hf, 'run', return_value={'params': []}) as run:
            with self.assertRaises(backend.AssistantError): backend.generate(request, backend.Cancellation())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:2], ['model', 'get'])

    def test_inpaint_rejects_incompatible_format_before_upload(self):
        request = backend.Request(Path('unused'), Path('unused'), 'paint', provider='higgsfield', mask=Path('mask'),
            provider_options={'model': 'nano_banana_pro', 'workspace': JOB, 'source_size': [1400,1000], 'aspect': 'auto'})
        with patch.object(hf, 'run') as run:
            with self.assertRaises(backend.AssistantError): backend.generate(request, backend.Cancellation())
        run.assert_not_called()

    def test_retrieve_never_submits_new_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'higgsfield-job.json').write_text(json.dumps({'job_ids': [JOB]}))
            with patch.object(hf, 'run', return_value={'status': 'completed', 'result_url': 'https://example.com/result.png'}) as run, \
                    patch.object(hf, 'download', return_value=b'PNG'):
                result = hf.retrieve(root, backend.Cancellation())
            self.assertEqual(run.call_args.args[0], ['generate', 'get', JOB])
            self.assertEqual(Path(result['path']).read_bytes(), b'PNG')


class AgentTests(unittest.TestCase):
    def test_structured_api_actions_and_no_image_generation(self):
        answer = {'reply': 'Добавлю лист.', 'actions': [{'tool': 'sheet.add', 'arguments': '{"width":320,"height":240}'}]}
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(answer)}]}]}
        with tempfile.TemporaryDirectory() as temp, patch.object(backend, 'api_post', return_value=response) as post:
            result = agent.ask(temp, {'question': 'Новый лист'}, backend.Cancellation(), 'api', 'secret')
        self.assertEqual(result['actions'][0]['arguments'], {'width': 320, 'height': 240})
        self.assertEqual(post.call_args.args[0], '/v1/responses')
        self.assertNotIn('tools', post.call_args.args[1])
        self.assertNotIn('secret', json.dumps(post.call_args.args[1]))

    def test_malformed_action_envelope_is_rejected(self):
        cases = [{'reply': 'ok', 'actions': [{'tool': 'sheet.add', 'arguments': 'print(1)'}]},
                 {'reply': 'ok', 'actions': [{'tool': 'sheet.add', 'arguments': '[]'}]},
                 {'reply': 'ok', 'actions': [{}]}, {'reply': False, 'actions': []}]
        for value in cases:
            response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}
            with tempfile.TemporaryDirectory() as temp, patch.object(backend, 'api_post', return_value=response):
                with self.assertRaises(backend.AssistantError): agent.ask(temp, {}, backend.Cancellation(), 'api', 'secret')

    def test_subscription_has_no_shell_apps_or_generation(self):
        captured = []
        def run(executable, args, cancel, **kwargs):
            captured.extend(args)
            Path(args[args.index('--output-last-message')+1]).write_text('{"reply":"Ready","actions":[]}', encoding='utf8')
            return 0, '', [{'type': 'turn.completed'}]
        with tempfile.TemporaryDirectory() as temp, patch.object(backend, 'discover_codex', return_value=Path('codex.exe')), \
                patch.object(backend, 'run_codex', side_effect=run):
            result = agent.ask(temp, {}, backend.Cancellation())
        self.assertEqual(result['reply'], 'Ready')
        self.assertEqual(captured[captured.index('--sandbox')+1], 'read-only')
        for feature in ('shell_tool', 'apps', 'plugins', 'multi_agent', 'image_generation'):
            self.assertIn(['--disable', feature], [captured[i:i+2] for i in range(len(captured)-1)])
