import IconRecorder from '@/assets/component/recorder.svg'
import IconRecorderActive from '@/assets/component/recorder@active.svg'
import { userState } from '@/store/user'
import { useUnmount } from 'ahooks'
import { Button } from 'antd'
import { useRef, useState } from 'react'

type MicSession = {
  stream: MediaStream
  context: AudioContext
  processor: ScriptProcessorNode
  source: MediaStreamAudioSourceNode
  silence: GainNode
  chunks: ArrayBuffer[]
  bytes: number
}

function asrUrl(token: string) {
  const base = import.meta.env.VITE_API_BASE || window.location.origin
  const url = new URL('/asr', base)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('token', token)
  return url.toString()
}

function toPcm16k(input: Float32Array, inputRate: number) {
  const ratio = inputRate / 16000
  const length = Math.max(1, Math.floor(input.length / ratio))
  const pcm = new Int16Array(length)
  for (let i = 0; i < length; i += 1) {
    const sample = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]))
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
  }
  return pcm.buffer
}

function joinPcm(chunks: ArrayBuffer[]) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0)
  const pcm = new Uint8Array(total)
  let offset = 0
  chunks.forEach((chunk) => {
    pcm.set(new Uint8Array(chunk), offset)
    offset += chunk.byteLength
  })
  return pcm.buffer
}

export default function Recorder(props: {
  onMessage?: (text: string) => void
}) {
  const { onMessage } = props
  const [phase, setPhase] = useState<'idle' | 'recording' | 'recognizing'>('idle')
  const sessionRef = useRef<MicSession | null>(null)
  const socketRef = useRef<WebSocket | null>(null)

  function releaseMic(session: MicSession) {
    session.stream.getTracks().forEach((track) => track.stop())
    session.processor.disconnect()
    session.source.disconnect()
    session.silence.disconnect()
    void session.context.close()
  }

  function recognize(pcm: ArrayBuffer) {
    const token = userState.token
    if (!token) {
      window.$app.message.error('请先登录')
      setPhase('idle')
      return
    }
    const ws = new WebSocket(asrUrl(token))
    socketRef.current = ws
    ws.binaryType = 'arraybuffer'
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      setPhase('idle')
      if (socketRef.current === ws) socketRef.current = null
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close()
      }
    }
    const timer = window.setTimeout(() => {
      window.$app.message.error('语音识别超时，请再试一次')
      finish()
    }, 60000)

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data as string)
      if (data.type === 'ready') {
        const step = 32 * 1024
        const bytes = new Uint8Array(pcm)
        for (let offset = 0; offset < bytes.byteLength; offset += step) {
          ws.send(bytes.slice(offset, offset + step))
        }
        ws.send(JSON.stringify({ type: 'stop' }))
        return
      }
      if (data.type === 'transcript' && data.text) {
        onMessage?.(data.text)
        return
      }
      if (data.type === 'error') {
        window.$app.message.error(data.message || '语音识别失败')
        finish()
        return
      }
      if (data.type === 'finished') finish()
    }
    ws.onerror = () => {
      window.$app.message.error('语音识别连接失败')
      finish()
    }
  }

  function stopASR() {
    const session = sessionRef.current
    if (!session) return
    sessionRef.current = null
    const pcm = joinPcm(session.chunks)
    releaseMic(session)
    if (pcm.byteLength < 3200) {
      window.$app.message.warning('录音太短，请再说一次')
      setPhase('idle')
      return
    }
    setPhase('recognizing')
    recognize(pcm)
  }

  async function startASR() {
    if (phase !== 'idle') return
    const token = userState.token
    if (!token) {
      window.$app.message.error('请先登录')
      return
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      window.$app.message.error(
        window.isSecureContext
          ? '当前浏览器不支持麦克风'
          : '请用 https 打开页面后再使用语音',
      )
      return
    }

    let stream: MediaStream | null = null
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })
      const context = new AudioContext()
      const source = context.createMediaStreamSource(stream)
      const processor = context.createScriptProcessor(4096, 1, 1)
      const silence = context.createGain()
      silence.gain.value = 0
      const session: MicSession = {
        stream,
        context,
        processor,
        source,
        silence,
        chunks: [],
        bytes: 0,
      }
      sessionRef.current = session
      processor.onaudioprocess = (event) => {
        if (sessionRef.current !== session || session.bytes >= 9_000_000) return
        const channel = event.inputBuffer.getChannelData(0)
        const chunk = toPcm16k(channel, context.sampleRate)
        session.chunks.push(chunk)
        session.bytes += chunk.byteLength
      }
      source.connect(processor)
      processor.connect(silence)
      silence.connect(context.destination)
      setPhase('recording')
    } catch (error: any) {
      stream?.getTracks().forEach((track) => track.stop())
      sessionRef.current = null
      if (error?.message?.includes('Permission denied') || error?.name === 'NotAllowedError') {
        window.$app.message.error('请开启麦克风权限')
      } else {
        window.$app.message.error(error?.message || '无法开始录音')
      }
    }
  }

  useUnmount(() => {
    const session = sessionRef.current
    sessionRef.current = null
    if (session) releaseMic(session)
    socketRef.current?.close()
  })

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      {phase === 'recording' ? (
        <Button shape="circle" variant="filled" danger onClick={stopASR}>
          <img src={IconRecorderActive} style={{ width: 20, height: 20 }} />
        </Button>
      ) : (
        <Button
          shape="circle"
          variant="filled"
          color="default"
          loading={phase === 'recognizing'}
          onClick={startASR}
        >
          <img src={IconRecorder} />
        </Button>
      )}
      {phase === 'recording' ? (
        <span style={{ color: '#d4380d', fontSize: 13 }}>正在听，再点一次结束</span>
      ) : null}
      {phase === 'recognizing' ? (
        <span style={{ color: '#666', fontSize: 13 }}>正在转成文字…</span>
      ) : null}
    </span>
  )
}
