import copy
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

from app.canvas_executor import generation_input
from app.canvas_references import bind_references
from app.canvas_service import graph_input_hash
from app.catalog import get_model
from app.dashscope import build_payload
from app.media_resolver import resolve_inputs


class CanvasReferenceTests(unittest.TestCase):
    def fixture(self, model='wan3.0-video-prime'):
        sources = [SimpleNamespace(id=f'{kind}-{i}', type=kind, data={'url': f'https://example.com/{kind}-{i}', 'name': f'{kind}-{i}'})
                   for kind in ('image', 'video', 'audio') for i in (1, 2)]
        prompt = SimpleNamespace(id='prompt', type='prompt', data={'text': '{{图片1}} 在 {{图片2}} 中行走'})
        bindings = [{'edge_id': f'{kind}-{i}', 'alias': f'{label}{i}', 'description': f'{label}{i}的用途'}
                    for kind, label in [('image', '图片'), ('video', '视频'), ('audio', '音频')] for i in (2, 1)]
        node = SimpleNamespace(id='generate', type='generate', data={
            'model': model, 'capability': 'r2v', 'resolution': '720P', 'ratio': '16:9', 'duration': 5,
            'inlinePrompt': '动作参考 {{视频1}}，配音参考 {{音频2}}', 'reference_bindings': bindings,
        })
        edges = [SimpleNamespace(id=source.id, source=source.id, target='generate', target_handle=f'{source.type}_0') for source in reversed(sources)]
        edges.append(SimpleNamespace(id='prompt-edge', source='prompt', target='generate', target_handle='prompt'))
        return SimpleNamespace(nodes=[*sources, prompt, node], edges=edges), node

    def test_real_generation_payload_matches_stable_tokens_across_all_media_kinds(self):
        canvas, node = self.fixture()
        frozen = generation_input(None, canvas, node, SimpleNamespace(role='admin'), Request({'type': 'http', 'headers': []}))
        self.assertIn('图 2 在 图 1 中行走\n\n动作参考 视频 2，配音参考 音频 1', frozen['prompt'])
        self.assertIn('图 2：图片1的用途', frozen['prompt'])
        model = get_model(node.data['model'])
        media = resolve_inputs(frozen['reference_media'], model, model.capability('r2v'))
        payload = build_payload(model=model.id, prompt=frozen['prompt'], resolution='720P', ratio='16:9', duration=5, media=media)
        self.assertEqual(payload['input']['media'], [
            {'type': f'reference_{kind}', 'url': f'https://example.com/{kind}-{i}'}
            for kind in ('image', 'video', 'audio') for i in (2, 1)
        ])
        self.assertNotIn('{{', payload['input']['prompt'])
        # Reloading edges in another database order cannot change explicit bindings.
        canvas.edges.reverse()
        again = generation_input(None, canvas, node, SimpleNamespace(role='admin'), Request({'type': 'http', 'headers': []}))
        self.assertEqual(again, frozen)

    def test_provider_specific_tags(self):
        media = [{'kind': 'image', '_edge_id': 'image', 'url': 'https://example.com/ref.jpg', 'name': '主角'}]
        bindings = [{'edge_id': 'image', 'alias': '图片1', 'description': '主角外观'}]
        for model, tag in [('wan3.0-video-prime', '图 1'), ('happyhorse-1.1-r2v', '[Image 1]'), ('MiniMax/MiniMax-H3', '第 1 张参考图片')]:
            with self.subTest(model=model):
                prompt, ordered = bind_references(model, '{{图片1}} 向前走', media, bindings)
                self.assertTrue(prompt.startswith(tag + ' 向前走'))
                self.assertIn(tag + '：主角外观', prompt)
                self.assertEqual(ordered[0]['url'], media[0]['url'])

    def test_disconnected_reference_is_rejected_instead_of_reassigned(self):
        media = [{'kind': 'image', '_edge_id': 'new-image', 'url': 'https://example.com/new.jpg', 'name': '新素材'}]
        old = [{'edge_id': 'removed-image', 'alias': '图片1', 'description': '旧主角'}]
        with self.assertRaisesRegex(HTTPException, '已断开或不存在'):
            bind_references('wan3.0-video-prime', '{{图片1}}', media, old)
        prompt, _ = bind_references('wan3.0-video-prime', '{{图片2}}', media, old)
        self.assertTrue(prompt.startswith('图 1'))

    def test_duplicate_reference_names_are_rejected(self):
        with self.assertRaisesRegex(HTTPException, '不能重复'):
            bind_references('wan3.0-video-prime', '场景', [], [
                {'edge_id': 'one', 'alias': '图片1'}, {'edge_id': 'two', 'alias': '图片1'},
            ])

    def test_mapping_and_descriptions_are_generation_inputs_but_layout_is_not(self):
        canvas, node = self.fixture()
        graph = {'nodes': [{'id': n.id, 'type': n.type, 'data': copy.deepcopy(n.data), 'position': {'x': 0, 'y': 0}} for n in canvas.nodes],
                 'edges': [{'id': e.id, 'source': e.source, 'target': e.target, 'source_handle': 'prompt' if e.source == 'prompt' else e.source.split('-')[0], 'target_handle': e.target_handle} for e in canvas.edges]}
        before = graph_input_hash(graph, node.id)
        graph['nodes'][-1]['position']['x'] = 999
        self.assertEqual(graph_input_hash(graph, node.id), before)
        graph['nodes'][-1]['data']['reference_bindings'][0]['description'] = '新的用途'
        self.assertNotEqual(graph_input_hash(graph, node.id), before)

    def test_legacy_reference_input_without_settings_keeps_its_prompt(self):
        prompt, media = bind_references('wan3.0-video-prime', '原始提示词', [
            {'kind': 'image', '_edge_id': 'image', 'url': 'https://example.com/ref.jpg', 'name': '主角'},
        ], [])
        self.assertEqual(prompt, '原始提示词')
        self.assertNotIn('_edge_id', media[0])
