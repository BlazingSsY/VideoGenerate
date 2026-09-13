import asyncio
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import agent_executor, canvas_executor, tasks
from app.config import settings
from app.database import Base, get_db
from app.models import AgentRun, AgentTask, CanvasNode, Message, User
from app.routers import canvas
from app.security import current_user


class CanvasRunTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.engine = create_engine(f"sqlite:///{self.directory}/test.db", connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        with self.Session() as db:
            user = User(username="runner", password_hash="x", role="admin")
            db.add(user)
            db.commit()
            self.user_id = user.id
        app = FastAPI()
        app.include_router(canvas.router)

        def get_test_db():
            with self.Session() as db:
                yield db

        def get_test_user():
            with self.Session() as db:
                return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[current_user] = get_test_user
        self.client = self.enterContext(TestClient(app))
        self.enterContext(patch.object(agent_executor, "SessionLocal", self.Session))
        self.enterContext(patch.object(tasks, "SessionLocal", self.Session))
        self.enterContext(patch("app.database.SessionLocal", self.Session))
        self.spawn = self.enterContext(patch.object(canvas, "spawn_run"))
        self.enterContext(patch.object(settings, "video_dir", self.directory))
        self.enterContext(patch.object(settings, "agent_run_concurrency", 2))
        self.canvas_id = self.client.post('/api/canvases', json={"title": "三镜头"}).json()["id"]
        self.base = f'/api/canvases/{self.canvas_id}'
        self.save_graph({
            "nodes": [
                *[{"id": f"shot-{i}", "type": "generate", "position": {"x": 0, "y": i * 200},
                   "data": {"name": f"镜头{i}", "model": "wan3.0-video-prime", "capability": "t2v",
                            "resolution": "720P", "ratio": "16:9", "duration": 5,
                            "inlinePrompt": f"场景{i}"}} for i in (1, 2, 3)],
                {"id": "out", "type": "output", "position": {"x": 500, "y": 0},
                 "data": {"items": [{"nodeKey": "shot-2"}, {"nodeKey": "shot-1"}],
                          "transitions": [{"after": "shot-2", "type": "fade"}]}},
            ],
            "edges": [{"id": f"edge-{i}", "source": f"shot-{i}", "source_handle": "output",
                       "target": "out", "target_handle": "input"} for i in (1, 2)],
        })

    def graph(self):
        return self.client.get(self.base).json()

    def save_graph(self, changes):
        graph = self.graph()
        graph.update(changes)
        response = self.client.put(self.base + '/graph', json=graph)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def start(self):
        response = self.client.post(self.base + '/run', json={"revision": self.graph()["revision"]})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def finish_message(self, message_id, failed=False):
        with self.Session() as db:
            message = db.get(Message, message_id)
            key = message.params["agent_node_id"]
            message.status = "failed" if failed else "succeeded"
            message.error = "测试失败" if failed else ""
            message.local_video = "" if failed else f"{key}.mp4"
            message.video_url = "" if failed else f"https://example.com/{key}.mp4"
            if not failed:
                (self.directory / message.local_video).write_bytes(b"video")
            db.commit()
        return key

    def complete_run(self, run):
        generated = []

        async def generate(message_id):
            generated.append(self.finish_message(message_id))

        with patch.object(agent_executor, 'run_generation', generate), patch.object(canvas_executor, 'compose_video_clips', AsyncMock(return_value='final.mp4')):
            asyncio.run(agent_executor.execute_run(run['id']))
        self.assertEqual(self.client.get(self.base + '/runs/latest').json()['status'], 'succeeded')
        return generated

    def test_incremental_run_reuses_successes_after_layout_and_output_order_changes(self):
        self.assertCountEqual(self.complete_run(self.start()), ['shot-1', 'shot-2', 'shot-3'])
        before = {node['id']: node['message_id'] for node in self.graph()['nodes'] if node['type'] == 'generate'}
        graph = self.graph()
        graph['nodes'][0]['position']['x'] += 200
        next(node for node in graph['nodes'] if node['id'] == 'out')['data']['items'].reverse()
        self.save_graph(graph)
        run = self.start()
        self.assertTrue(all(task['status'] == 'succeeded' for task in run['tasks'] if task['task_type'] == 'generate'))
        self.assertEqual(self.complete_run(run), [])
        self.assertEqual({node['id']: node['message_id'] for node in self.graph()['nodes'] if node['type'] == 'generate'}, before)
        with self.Session() as db:
            self.assertEqual(db.query(Message).count(), 3)
            output = db.query(AgentTask).filter_by(run_id=run['id'], node_id='out').one()
            self.assertEqual(output.input_snapshot['inputs'], ['shot-1', 'shot-2'])
            self.assertEqual(output.status, 'succeeded')

    def test_incremental_run_generates_only_edited_and_added_nodes(self):
        self.complete_run(self.start())
        graph = self.graph()
        graph['nodes'][0]['data']['inlinePrompt'] = '修改后的镜头'
        graph['nodes'].append({**graph['nodes'][1], 'id': 'shot-4', 'data': {**graph['nodes'][1]['data'], 'inlinePrompt': '新增镜头'}})
        self.save_graph(graph)
        self.assertCountEqual(self.complete_run(self.start()), ['shot-1', 'shot-4'])

    def test_incremental_run_invalidates_consumers_when_upstream_video_changes(self):
        graph = self.graph()
        graph['nodes'][1]['data']['capability'] = 'r2v'
        graph['edges'].append({'id': 'reference', 'source': 'shot-1', 'source_handle': 'output', 'target': 'shot-2', 'target_handle': 'video_0'})
        self.save_graph(graph)
        self.complete_run(self.start())
        self.assertEqual(self.complete_run(self.start()), [])
        # A manual regeneration may yield another video from identical inputs.
        with self.Session() as db:
            upstream = db.get(Message, db.get(CanvasNode, 'shot-1').message_id)
            upstream.video_url = 'https://example.com/new-upstream.mp4'
            db.commit()
        self.assertEqual(self.complete_run(self.start()), ['shot-2'])
        graph = self.graph()
        graph['nodes'][0]['data']['duration'] = 10
        self.save_graph(graph)
        self.assertCountEqual(self.complete_run(self.start()), ['shot-1', 'shot-2'])

    def test_incremental_run_downloads_reused_online_video_without_regenerating(self):
        self.complete_run(self.start())
        with self.Session() as db:
            message = db.get(Message, db.get(CanvasNode, 'shot-1').message_id)
            message_id = message.id
            message.local_video = ''
            db.commit()
        (self.directory / 'reused.mp4').write_bytes(b'cached')
        download = AsyncMock(return_value='reused.mp4')
        with patch.object(agent_executor, '_download_video', download):
            self.assertEqual(self.complete_run(self.start()), [])
        download.assert_awaited_once_with('https://example.com/shot-1.mp4', message_id)
        with self.Session() as db:
            self.assertEqual(db.get(Message, message_id).local_video, 'reused.mp4')

    def test_incremental_run_reuses_a_video_created_by_single_node_run(self):
        with patch.object(canvas, 'spawn'):
            response = self.client.post(self.base + '/nodes/shot-1/run')
        self.assertEqual(response.status_code, 202)
        message_id = response.json()['message_id']
        with self.Session() as db:
            message = db.get(Message, message_id)
            message.status = 'succeeded'
            message.video_url = 'https://example.com/manual.mp4'
            message.local_video = 'manual.mp4'
            (self.directory / message.local_video).write_bytes(b'manual')
            db.commit()
        self.assertCountEqual(self.complete_run(self.start()), ['shot-2', 'shot-3'])
        self.assertEqual(next(node for node in self.graph()['nodes'] if node['id'] == 'shot-1')['message_id'], message_id)

    def test_failed_reuse_download_never_submits_a_new_generation(self):
        self.complete_run(self.start())
        with self.Session() as db:
            message = db.get(Message, db.get(CanvasNode, 'shot-1').message_id)
            message.local_video = ''
            db.commit()
        run = self.start()
        with patch.object(agent_executor, '_download_video', AsyncMock(return_value='')), patch.object(agent_executor, 'run_generation', AsyncMock()) as generate:
            asyncio.run(agent_executor.execute_run(run['id']))
        generate.assert_not_awaited()
        self.assertEqual(self.client.get(self.base + '/runs/latest').json()['status'], 'failed')

    def test_incremental_run_retries_failed_and_expired_nodes(self):
        self.complete_run(self.start())
        with self.Session() as db:
            db.get(Message, db.get(CanvasNode, 'shot-1').message_id).status = 'failed'
            db.get(Message, db.get(CanvasNode, 'shot-2').message_id).video_expired = True
            db.commit()
        self.assertCountEqual(self.complete_run(self.start()), ['shot-1', 'shot-2'])

    def test_start_freezes_all_generators_and_order_rejects_duplicate_and_stale_starts(self):
        run = self.start()
        self.assertEqual(len(run["tasks"]), 4)  # includes the unconnected third generator
        self.assertEqual(self.client.post(self.base + '/run', json={"revision": self.graph()["revision"]}).status_code, 409)
        self.assertEqual(self.client.post(self.base + '/nodes/shot-1/run').status_code, 409)
        self.assertEqual(self.client.post(self.base + '/run', json={"revision": 0}).status_code, 409)
        with self.Session() as db:
            output = db.query(AgentTask).filter_by(run_id=run["id"], node_id="out").one()
            self.assertEqual(output.input_snapshot["inputs"], ["shot-2", "shot-1"])
            self.assertEqual(output.input_snapshot["transitions"], [{"after": "shot-2", "type": "fade"}])
        self.assertEqual(self.client.get(self.base + '/runs/latest').json()["id"], run["id"])
        self.assertTrue(all(n["status"] == "queued" for n in self.graph()["nodes"]))
        self.spawn.assert_called_once_with(run["id"])

    def test_validation_is_atomic_before_any_generation_is_submitted(self):
        graph = self.graph()
        graph["nodes"][2]["data"]["inlinePrompt"] = ""
        self.save_graph(graph)
        response = self.client.post(self.base + '/run', json={"revision": self.graph()["revision"]})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("镜头3", response.json()["detail"])
        with self.Session() as db:
            self.assertEqual(db.query(AgentRun).count(), 0)
            self.assertEqual(db.query(Message).count(), 0)
        self.spawn.assert_not_called()

    def test_concurrent_start_only_creates_one_run(self):
        payload = {"revision": self.graph()["revision"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post(self.base + '/run', json=payload), range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [202, 409])
        with self.Session() as db:
            self.assertEqual(db.query(AgentRun).count(), 1)

    def test_run_generates_in_parallel_composes_in_saved_order_and_restores_videos(self):
        run = self.start()
        active = set()
        finished = []
        peak = 0

        async def generate(message_id):
            nonlocal peak
            active.add(message_id)
            peak = max(peak, len(active))
            await asyncio.sleep(0.02)
            finished.append(self.finish_message(message_id))
            active.remove(message_id)

        async def compose(clips, _canvas_id):
            self.assertIn("shot-1", finished)
            self.assertIn("shot-2", finished)
            self.assertEqual(clips, [("shot-2.mp4", "fade"), ("shot-1.mp4", "none")])
            (self.directory / 'final.mp4').write_bytes(b'joined')
            return 'final.mp4'

        with patch.object(agent_executor, "run_generation", generate), patch.object(canvas_executor, "compose_video_clips", compose):
            asyncio.run(agent_executor.execute_run(run["id"]))
        self.assertEqual(peak, 2)
        self.assertCountEqual(finished, ['shot-1', 'shot-2', 'shot-3'])
        latest = self.client.get(self.base + '/runs/latest').json()
        self.assertEqual(latest["status"], 'succeeded', latest)
        statuses = self.client.get(self.base + '/status').json()
        self.assertTrue(all(node["video_src"] for node in statuses), statuses)
        self.assertEqual(next(n for n in self.graph()["nodes"] if n["id"] == 'out')["data"]["outputFile"], 'final.mp4')

    def test_failure_blocks_only_dependent_output_and_finishes_other_shots(self):
        run = self.start()
        finished = []

        async def generate(message_id):
            with self.Session() as db:
                key = db.get(Message, message_id).params["agent_node_id"]
            finished.append(self.finish_message(message_id, failed=key == 'shot-1'))

        with patch.object(settings, "agent_run_concurrency", 1), patch.object(agent_executor, "run_generation", generate), patch.object(agent_executor, "_compose") as compose:
            asyncio.run(agent_executor.execute_run(run["id"]))
        self.assertCountEqual(finished, ['shot-1', 'shot-2', 'shot-3'])
        compose.assert_not_called()
        latest = self.client.get(self.base + '/runs/latest').json()
        self.assertEqual(latest["status"], 'failed')
        self.assertEqual({t['node_id']: t['status'] for t in latest['tasks']}, {
            'shot-1': 'failed', 'shot-2': 'succeeded', 'shot-3': 'succeeded', 'out': 'blocked',
        })
        self.assertIn('失败', next(n for n in self.client.get(self.base + '/status').json() if n['node_id'] == 'out')['error'])

    def test_edits_do_not_change_frozen_run_or_receive_stale_output(self):
        run = self.start()
        graph = self.graph()
        graph['nodes'][0]['data']['inlinePrompt'] = '新提示词'
        graph['nodes'][1]['position']['x'] += 200
        self.save_graph(graph)
        prompts = []

        async def generate(message_id):
            with self.Session() as db:
                prompts.append(db.get(Message, message_id).resolved_prompt)
            self.finish_message(message_id)

        async def compose(*_):
            return 'old-final.mp4'

        with patch.object(agent_executor, 'run_generation', generate), patch.object(canvas_executor, 'compose_video_clips', compose):
            asyncio.run(agent_executor.execute_run(run['id']))
        self.assertIn('场景1', prompts)
        current = {n['id']: n for n in self.graph()['nodes']}
        self.assertIsNone(current['shot-1']['message_id'])
        self.assertEqual(current['shot-2']['status'], 'succeeded')
        self.assertNotIn('outputFile', current['out']['data'])

    def test_generated_reference_waits_for_this_runs_upstream_video(self):
        graph = self.graph()
        graph['nodes'][1]['data']['capability'] = 'r2v'
        graph['edges'].append({'id': 'reference', 'source': 'shot-1', 'source_handle': 'output', 'target': 'shot-2', 'target_handle': 'video_0'})
        self.save_graph(graph)
        run = self.start()
        finished = set()

        async def generate(message_id):
            with self.Session() as db:
                message = db.get(Message, message_id)
                if message.params['agent_node_id'] == 'shot-2':
                    self.assertIn('shot-1', finished)
                    self.assertEqual(message.reference_media[0]['url'], 'https://example.com/shot-1.mp4')
            finished.add(self.finish_message(message_id))

        async def compose(*_):
            return 'final.mp4'

        with patch.object(agent_executor, 'run_generation', generate), patch.object(canvas_executor, 'compose_video_clips', compose):
            asyncio.run(agent_executor.execute_run(run['id']))
        self.assertEqual(self.client.get(self.base + '/runs/latest').json()['status'], 'succeeded')

    def test_reference_bindings_freeze_prompt_and_media_order_in_whole_canvas_run(self):
        graph = self.graph()
        shot = graph['nodes'][1]
        shot['data'].update({
            'capability': 'r2v', 'inlinePrompt': '{{图片1}} 在 {{图片2}} 中行走，动作参考 {{视频1}}，声音参考 {{音频1}}',
            'reference_bindings': [
                {'edge_id': 'image-2', 'alias': '图片2', 'description': '街景'},
                {'edge_id': 'image-1', 'alias': '图片1', 'description': '主角'},
                {'edge_id': 'video-file', 'alias': '视频2', 'description': '镜头'},
                {'edge_id': 'upstream', 'alias': '视频1', 'description': '前一镜头'},
                {'edge_id': 'audio', 'alias': '音频1', 'description': '配音'},
            ],
        })
        for key, kind in [('image-1', 'image'), ('image-2', 'image'), ('video-file', 'video'), ('audio', 'audio')]:
            graph['nodes'].append({'id': key, 'type': kind, 'position': {'x': 0, 'y': 0},
                                   'data': {'name': key, 'url': f'https://example.com/{key}'}})
            graph['edges'].append({'id': key, 'source': key, 'source_handle': kind, 'target': 'shot-2', 'target_handle': f'{kind}_0'})
        graph['edges'].append({'id': 'upstream', 'source': 'shot-1', 'source_handle': 'output', 'target': 'shot-2', 'target_handle': 'video_0'})
        self.save_graph(graph)
        run = self.start()
        with self.Session() as db:
            snapshot = db.query(AgentTask).filter_by(run_id=run['id'], node_id='shot-2').one().input_snapshot
            self.assertTrue(snapshot['prompt'].startswith('图 2 在 图 1 中行走，动作参考 视频 2，声音参考 音频 1'))
            self.assertEqual(snapshot['depends_on'], ['shot-1'])
        graph = self.graph()
        data = next(node for node in graph['nodes'] if node['id'] == 'shot-2')['data']
        data['reference_bindings'].reverse()
        data['reference_bindings'][0]['description'] = '编辑后的用途'
        self.save_graph(graph)
        observed = []

        async def generate(message_id):
            with self.Session() as db:
                message = db.get(Message, message_id)
                if message.params['agent_node_id'] == 'shot-2':
                    observed.append(message.resolved_prompt)
                    self.assertEqual(message.resolved_prompt, snapshot['prompt'])
                    self.assertEqual([item['url'] for item in message.reference_media], [
                        'https://example.com/image-2', 'https://example.com/image-1',
                        'https://example.com/video-file', 'https://example.com/shot-1.mp4', 'https://example.com/audio',
                    ])
            self.finish_message(message_id)

        with patch.object(agent_executor, 'run_generation', generate), patch.object(canvas_executor, 'compose_video_clips', AsyncMock(return_value='final.mp4')):
            asyncio.run(agent_executor.execute_run(run['id']))
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.client.get(self.base + '/runs/latest').json()['status'], 'succeeded')
        self.assertIsNone(next(node for node in self.graph()['nodes'] if node['id'] == 'shot-2')['message_id'])

    def test_recovery_reuses_completed_message_and_skips_standalone_worker(self):
        run = self.start()
        with self.Session() as db:
            shot = db.query(AgentTask).filter_by(run_id=run['id'], node_id='shot-1').one()
            snapshot = shot.input_snapshot
        message_id = agent_executor._create_message(run['id'], snapshot)
        with patch.object(tasks, 'spawn') as ordinary_spawn:
            asyncio.run(tasks.resume_unfinished())
            ordinary_spawn.assert_not_called()
        self.finish_message(message_id)
        with patch.object(agent_executor, 'run_generation') as generate:
            self.assertTrue(asyncio.run(agent_executor._execute_task(run['id'], 'shot-1', run['turn_id'])))
            generate.assert_not_called()
        with patch.object(agent_executor, 'spawn_run') as resumed:
            asyncio.run(agent_executor.resume_runs())
            resumed.assert_called_once_with(run['id'])

    def test_manual_compose_persists_video_and_ignores_disconnected_or_excluded_clips(self):
        graph = self.graph()
        output = next(n for n in graph['nodes'] if n['id'] == 'out')
        output['data']['excluded'] = ['shot-2']
        output['data']['items'].append({'nodeKey': 'shot-3'})
        self.save_graph(graph)
        with self.Session() as db:
            for node in db.query(CanvasNode).filter_by(type='generate').all():
                message = Message(conversation_id=self.canvas_id, role='assistant', status='succeeded', local_video=f'{node.id}.mp4')
                db.add(message)
                db.flush()
                node.message_id = message.id
            db.commit()

        async def compose(clips, _id):
            self.assertEqual(clips, [('shot-1.mp4', 'none')])
            return 'manual.mp4'

        with patch.object(canvas_executor, 'compose_video_clips', compose):
            response = self.client.post(self.base + '/output/out/compose')
        self.assertEqual(response.status_code, 200, response.text)
        output = next(n for n in self.client.get(self.base + '/status').json() if n['node_id'] == 'out')
        self.assertIn('manual.mp4', output['video_src'])
        self.assertEqual(output['status'], 'succeeded')

    def test_run_and_progress_are_owner_scoped(self):
        self.start()
        with self.Session() as db:
            other = User(username='other', password_hash='x', role='admin')
            db.add(other)
            db.commit()
            self.user_id = other.id
        self.assertEqual(self.client.get(self.base + '/runs/latest').status_code, 404)
        self.assertEqual(self.client.post(self.base + '/run', json={'revision': 1}).status_code, 404)

    def test_scheduled_run_downloads_clips_even_when_regular_downloads_are_disabled(self):
        run = self.start()
        with self.Session() as db:
            snapshot = db.query(AgentTask).filter_by(run_id=run['id'], node_id='shot-1').one().input_snapshot
        message_id = agent_executor._create_message(run['id'], snapshot)
        result = {'status': tasks.TERMINAL_OK, 'video_url': 'https://example.com/clip.mp4'}
        with patch.object(settings, 'download_videos', False), \
             patch.object(tasks, 'fetch_task', AsyncMock(return_value=result)), \
             patch.object(tasks, '_download_video', AsyncMock(return_value='local.mp4')) as download:
            asyncio.run(tasks._poll_until_done(SimpleNamespace(api_key='test', base_url='https://example.com'), 'task', message_id, time.monotonic()))
            download.assert_awaited_once()
        with self.Session() as db:
            self.assertEqual(db.get(Message, message_id).local_video, 'local.mp4')

    def test_single_node_rerun_clears_previous_run_error(self):
        with self.Session() as db:
            node = db.get(CanvasNode, 'shot-1')
            node.status = 'failed'
            node.data = {**node.data, 'outputError': '上次运行失败'}
            db.commit()
        with patch.object(canvas, 'spawn'):
            response = self.client.post(self.base + '/nodes/shot-1/run')
        self.assertEqual(response.status_code, 202, response.text)
        node = next(n for n in self.client.get(self.base + '/status').json() if n['node_id'] == 'shot-1')
        self.assertEqual(node['status'], 'pending')
        self.assertEqual(node['error'], '')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_ffmpeg_output_is_playable(self):
        for name, color in [('first', 'red'), ('second', 'blue')]:
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=320x180:r=25',
                            '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(self.directory / f'{name}.mp4')], check=True)
        filename = asyncio.run(canvas_executor.compose_video_clips([('second.mp4', 'none'), ('first.mp4', 'none')], self.canvas_id))
        probe = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_name',
                                         '-of', 'json', str(self.directory / filename)])
        result = json.loads(probe)
        self.assertAlmostEqual(float(result['format']['duration']), 2, delta=0.15)
        self.assertEqual(result['streams'][0]['codec_name'], 'h264')
