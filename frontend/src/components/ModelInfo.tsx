import { Alert, Drawer, Space, Table, Tag, Typography } from 'antd'
import { CheckCircleFilled, MinusOutlined } from '@ant-design/icons'

import type { MatrixRow, ModeId, VideoModel } from '../types'
import { MODE_COLORS } from '../types'
import { capabilityMediaInputs, minimumMedia } from '../mediaCapabilities'

interface Props {
  open: boolean
  onClose: () => void
  models: VideoModel[]      // 当前账号可用的模型（带完整参数）
  matrix: MatrixRow[]       // 全部模型的能力对照（含无权使用的）
  modeLabels: Record<string, string>
  maxDuration: number
}

const MODES: ModeId[] = ['t2v', 'i2v', 'r2v']

const MODE_EXPLAIN: Record<ModeId, string> = {
  t2v: '只给文字提示词，模型凭空生成画面。',
  i2v: '首帧图会严格成为视频的第一帧，适合让一张确定的静态画面动起来。',
  r2v: '图片、视频或音频只作为人物、物体、动作、风格与声音参考，不会被强制作为第一帧。',
}

function mediaSummary(model: VideoModel, capabilityId: ModeId) {
  const capability = model.capabilities.find((item) => item.id === capabilityId)
  if (!capability) return ''
  const specs = capabilityMediaInputs(capability)
  if (!specs.length) return '无需素材'
  const parts = specs.map((spec) => {
    if (spec.min_count === spec.max_count) return `${spec.label} ${spec.min_count} 个`
    if (spec.min_count > 0) return `${spec.label} ${spec.min_count}-${spec.max_count} 个`
    return `${spec.label}最多 ${spec.max_count} 个`
  })
  const required = minimumMedia(capability)
  return `${parts.join('；')}${required > 0 && specs.every((spec) => spec.min_count === 0) ? `；至少任选 ${required} 个素材` : ''}`
}

export default function ModelInfo({
  open,
  onClose,
  models,
  matrix,
  modeLabels,
  maxDuration,
}: Props) {
  return (
    <Drawer open={open} onClose={onClose} title="模型能力说明" width={720}>
      <Typography.Paragraph type="secondary">
        参数取值依据阿里云百炼官方 API 文档整理。系统全局限制视频时长最长 {maxDuration} 秒，
        因此下方展示的时长范围可能小于官方上限。
      </Typography.Paragraph>

      <Space direction="vertical" size={4} style={{ marginBottom: 20 }}>
        {MODES.map((mode) => (
          <div key={mode}>
            <Tag color={MODE_COLORS[mode]}>{modeLabels[mode] || mode}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {MODE_EXPLAIN[mode]}
            </Typography.Text>
          </div>
        ))}
      </Space>

      <Typography.Title level={5}>哪个模型支持哪种生成方式</Typography.Title>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        dataSource={matrix}
        style={{ marginBottom: 24 }}
        columns={[
          {
            title: '模型',
            dataIndex: 'label',
            render: (label: string, row: MatrixRow) => (
              <Space direction="vertical" size={0}>
                <a href={row.doc_url} target="_blank" rel="noreferrer">
                  {label}
                </a>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {row.id}
                </Typography.Text>
              </Space>
            ),
          },
          ...MODES.map((mode) => ({
            title: modeLabels[mode] || mode,
            align: 'center' as const,
            width: 96,
            render: (_: unknown, row: MatrixRow) =>
              row.modes.includes(mode) ? (
                <CheckCircleFilled style={{ color: '#7f8cff' }} />
              ) : (
                <MinusOutlined style={{ color: '#43444f' }} />
              ),
          })),
          {
            title: '你的权限',
            width: 90,
            align: 'center',
            render: (_: unknown, row: MatrixRow) =>
              row.allowed ? <Tag color="green">可用</Tag> : <Tag>无权限</Tag>,
          },
        ]}
      />

      <Typography.Title level={5}>可用模型的参数范围</Typography.Title>
      {models.map((model) => (
        <div key={model.id} style={{ marginBottom: 18 }}>
          <Space size={6} wrap>
            <Typography.Text strong>{model.label}</Typography.Text>
            {model.capabilities.map((c) => (
              <Tag key={c.id} color={MODE_COLORS[c.id]}>
                {c.label}
              </Tag>
            ))}
          </Space>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {model.description}
            </Typography.Text>
          </div>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text-dim)' }}>
            <li>
              分辨率：{model.resolutions.join(' / ')}（默认 {model.default_resolution}）
              {model.resolution_note ? ' — ' + model.resolution_note : ''}
            </li>
            <li>
              画面比例：
              {model.ratios.length > 0
                ? model.ratios.map((r) => (r === 'adaptive' ? '自适应' : r)).join(' / ')
                : '跟随首帧图，无此参数'}
            </li>
            <li>
              时长：{model.durations[0]}-{model.durations[model.durations.length - 1]} 秒
              {model.duration_max > maxDuration
                ? `（官方上限 ${model.duration_max} 秒，已按系统配置收窄）`
                : ''}
            </li>
            <li>
              水印：{model.supports_watermark ? `支持，默认${model.watermark_default ? '开' : '关'}` : '不支持'}
              {model.supports_audio ? ' ｜ 音频：支持原生对白/BGM/音效' : ''}
            </li>
            {model.capabilities
              .filter((c) => capabilityMediaInputs(c).length > 0)
              .map((c) => (
                <li key={c.id}>
                  {c.label}：{mediaSummary(model, c.id)}
                </li>
              ))}
          </ul>
          {model.notes.length > 0 && (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 8 }}
              message={
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
                  {model.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              }
            />
          )}
        </div>
      ))}
    </Drawer>
  )
}
