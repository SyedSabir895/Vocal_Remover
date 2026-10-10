import { useEffect, useRef, useState } from 'react'
import { processSong, STEMS } from './api.js'
import './App.css'

const ACCEPT = '.mp3,.wav,.flac,.m4a,.ogg,.aac'
const MAX_MB = 50
const TRACK_INFO = {
  instrumental: { title: 'Instrumental', hint: 'Music only, vocals removed' },
  vocals: { title: 'Vocals', hint: 'Voice only' },
}

function App() {
  const [phase, setPhase] = useState('idle')
  const [status, setStatus] = useState({ text: '', progress: null })
  const [songName, setSongName] = useState('')
  const [tracks, setTracks] = useState([])
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  // Bumped on every new run or reset, so a stale run stops touching state.
  const runId = useRef(0)
  const objectUrls = useRef([])

  const releaseUrls = () => {
    objectUrls.current.forEach((url) => URL.revokeObjectURL(url))
    objectUrls.current = []
  }

  useEffect(() => releaseUrls, [])

  const reset = () => {
    runId.current += 1
    releaseUrls()
    setTracks([])
    setError('')
    setPhase('idle')
  }

  const handleFile = async (file) => {
    if (!file) return
    reset()
    const id = runId.current
    const stale = () => runId.current !== id

    if (file.size > MAX_MB * 1024 * 1024) {
      setError(`File too large. Limit is ${MAX_MB} MB.`)
      setPhase('error')
      return
    }

    setSongName(file.name.replace(/\.[^.]+$/, ''))
    setStatus({ text: 'Starting…', progress: null })
    setPhase('busy')
    try {
      const blobs = await processSong(file, (update) => !stale() && setStatus(update), stale)
      if (stale() || !blobs) return
      // Results are held in browser memory only.
      const urls = blobs.map((blob) => URL.createObjectURL(blob))
      objectUrls.current = urls
      setTracks(STEMS.map((stem, index) => ({ stem, ...TRACK_INFO[stem], url: urls[index] })))
      setPhase('done')
    } catch (failure) {
      if (stale()) return
      setError(failure.message)
      setPhase('error')
    }
  }

  const onDrop = (event) => {
    event.preventDefault()
    setDragging(false)
    handleFile(event.dataTransfer.files[0])
  }

  const indeterminate = status.progress === null

  return (
    <main className="app">
      <header>
        <h1>Vocals Remover</h1>
        <p>Upload a song, get the instrumental and the vocals as separate tracks.</p>
      </header>

      {(phase === 'idle' || phase === 'error') && (
        <label
          className={`dropzone${dragging ? ' dragging' : ''}`}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            type="file"
            accept={ACCEPT}
            onChange={(event) => {
              handleFile(event.target.files[0])
              event.target.value = ''
            }}
          />
          <strong>Drop a song here or click to choose</strong>
          <span>MP3, WAV, FLAC, M4A, OGG, AAC · up to {MAX_MB} MB · max 10 minutes</span>
        </label>
      )}

      {phase === 'error' && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {phase === 'busy' && (
        <section className="card">
          <h2>{songName}</h2>
          <div
            className={`bar${indeterminate ? ' indeterminate' : ''}`}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={indeterminate ? undefined : status.progress}
          >
            <div style={{ width: indeterminate ? undefined : `${status.progress}%` }} />
          </div>
          <p className="status">{status.text}</p>
          <p className="note">This can take a few minutes. Keep this tab open.</p>
        </section>
      )}

      {phase === 'done' && (
        <section className="card">
          <h2>{songName}</h2>
          {tracks.map((track) => (
            <div className="track" key={track.stem}>
              <div className="track-head">
                <div>
                  <strong>{track.title}</strong>
                  <span>{track.hint}</span>
                </div>
                <a className="button" href={track.url} download={`${songName} (${track.stem}).mp3`}>
                  Download
                </a>
              </div>
              <audio controls src={track.url} />
            </div>
          ))}
          <button type="button" className="button secondary" onClick={reset}>
            Process another song
          </button>
          <p className="note">Tracks stay in this tab only. They are gone once you close or reload it.</p>
        </section>
      )}
    </main>
  )
}

export default App
