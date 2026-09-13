import { useState, useEffect, useRef } from 'react'
import { Plus, Trash2, Sparkles, X } from 'lucide-react'
import api, { errorText } from '../api'
import { cn } from '../lib/utils'

interface Skill {
  id: string; name: string; description: string; instructions: string
  enabled: boolean; plan_shape: string; max_nodes: number
  requires: Record<string, any>; inputs: any[]
}

const PLAN_SHAPES = [
  { value: 'single', label: '单镜头' },
  { value: 'sequence', label: '顺序多镜头' },
  { value: 'parallel', label: '并行多镜头' },
] as const

export default function Skills() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [error, setError] = useState('')
  const formRef = useRef<{ name: HTMLInputElement; description: HTMLInputElement; instructions: HTMLTextAreaElement }>(null!)
  const [planShape, setPlanShape] = useState<string>('single')
  const [maxNodes, setMaxNodes] = useState(1)
  const [requiresJson, setRequiresJson] = useState('{}')
  const [inputsJson, setInputsJson] = useState('[]')

  const load = async () => { const res = await api.get('/api/skills'); setSkills(res.data) }
  useEffect(() => { load() }, [])

  const add = async () => {
    const f = formRef.current
    if (!f) return
    const name = f.name.value.trim()
    const instructions = f.instructions.value.trim()
    if (!name || !instructions) { setError('名称和规则不能为空'); return }
    let requires: Record<string, any>
    let inputs: any[]
    try {
      requires = JSON.parse(requiresJson)
      inputs = JSON.parse(inputsJson)
    } catch {
      setError('requires / inputs 必须是合法 JSON'); return
    }
    if (typeof requires !== 'object' || requires === null || Array.isArray(requires)) {
      setError('requires 必须是 JSON 对象'); return
    }
    if (!Array.isArray(inputs)) { setError('inputs 必须是 JSON 数组'); return }
    try {
      await api.post('/api/skills', {
        name, description: f.description.value, instructions, enabled: true,
        plan_shape: planShape, max_nodes: maxNodes, requires, inputs,
      })
      setShowAdd(false); setError('')
      setPlanShape('single'); setMaxNodes(1); setRequiresJson('{}'); setInputsJson('[]')
      await load()
    } catch (e) { setError(errorText(e, '创建失败')) }
  }

  const remove = async (id: string) => { await api.delete(`/api/skills/${id}`); await load() }
  const toggle = async (s: Skill) => { await api.patch(`/api/skills/${s.id}`, { enabled: !s.enabled }); await load() }

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[var(--color-bg)]">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-lg font-semibold text-[var(--color-ink)]">技能管理</h1>
          <button onClick={() => setShowAdd(!showAdd)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-white bg-brand-gradient rounded-lg hover:shadow-md">
            <Plus className="w-3.5 h-3.5" /> 新建技能
          </button>
        </div>

        {showAdd && (
          <div className="mb-4 p-4 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl animate-fade-in space-y-3">
            {error && <p className="text-xs text-[var(--color-danger)]">{error}</p>}
            <input ref={el => { if (el) formRef.current = { ...formRef.current!, name: el } }} placeholder="技能名称" className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)]" />
            <input ref={el => { if (el) formRef.current = { ...formRef.current!, description: el } }} placeholder="一句话描述" className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)]" />
            <textarea ref={el => { if (el) formRef.current = { ...formRef.current!, instructions: el } }} placeholder="技能规则（注入给 LLM 的指令）" rows={4} className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)] placeholder:text-[var(--color-ink-tertiary)] resize-none" />

            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-[11px] text-[var(--color-ink-secondary)]">计划形态</span>
                <select value={planShape} onChange={e => setPlanShape(e.target.value)}
                  className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)]">
                  {PLAN_SHAPES.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-[11px] text-[var(--color-ink-secondary)]">最大镜头数（1-10）</span>
                <input type="number" min={1} max={10} value={maxNodes}
                  onChange={e => setMaxNodes(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                  className="w-full px-3 py-2 text-sm bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)]" />
              </label>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-[11px] text-[var(--color-ink-secondary)]">requires（JSON 对象，如 {"{\"capability\": \"t2v\"}"}）</span>
                <textarea value={requiresJson} onChange={e => setRequiresJson(e.target.value)} rows={3}
                  className="w-full px-3 py-2 text-xs font-mono bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)] resize-none" />
              </label>
              <label className="space-y-1">
                <span className="text-[11px] text-[var(--color-ink-secondary)]">inputs（JSON 数组，如中段素材槽声明）</span>
                <textarea value={inputsJson} onChange={e => setInputsJson(e.target.value)} rows={3}
                  className="w-full px-3 py-2 text-xs font-mono bg-[var(--color-surface-3)] border border-[var(--color-border)] rounded-lg outline-none text-[var(--color-ink)] resize-none" />
              </label>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 text-xs text-[var(--color-ink-secondary)] hover:text-[var(--color-ink)]">取消</button>
              <button onClick={add} className="px-4 py-1.5 text-xs text-white bg-brand-gradient rounded-lg">创建</button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {skills.map(s => (
            <div key={s.id} className="p-4 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-lg">
              <div className="flex items-center gap-2 mb-1">
                <Sparkles className="w-3.5 h-3.5 text-[var(--color-primary)]" />
                <span className="text-sm font-medium text-[var(--color-ink)]">{s.name}</span>
                <span className="px-1.5 py-0.5 text-[10px] rounded bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)]">{s.plan_shape || 'single'}</span>
                <span className="px-1.5 py-0.5 text-[10px] rounded bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)]">≤{s.max_nodes || 1} 镜头</span>
                {s.enabled ? (
                  <span className="px-1.5 py-0.5 text-[10px] rounded bg-green-500/10 text-green-400">已启用</span>
                ) : (
                  <span className="px-1.5 py-0.5 text-[10px] rounded bg-[var(--color-surface-3)] text-[var(--color-ink-tertiary)]">已停用</span>
                )}
                <div className="ml-auto flex items-center gap-1">
                  <button onClick={() => toggle(s)} className={cn('px-2 py-1 text-[10px] rounded transition-colors', s.enabled ? 'text-[var(--color-ink-tertiary)] hover:text-[var(--color-warning)]' : 'text-green-400 hover:bg-green-950/30')}>
                    {s.enabled ? '停用' : '启用'}
                  </button>
                  <button onClick={() => remove(s.id)} className="p-1 rounded hover:bg-red-950/50 text-[var(--color-ink-tertiary)] hover:text-[var(--color-danger)]">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              {s.description && <p className="text-xs text-[var(--color-ink-secondary)] mb-1">{s.description}</p>}
              <p className="text-[11px] text-[var(--color-ink-tertiary)] line-clamp-2">{s.instructions}</p>
            </div>
          ))}
          {skills.length === 0 && <p className="text-xs text-[var(--color-ink-tertiary)] text-center py-8">还没有技能</p>}
        </div>
      </div>
    </div>
  )
}
