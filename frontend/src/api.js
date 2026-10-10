const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
// When set (e.g. "username/vocals-remover"), songs are processed by that Hugging Face
// Space instead of the local Python backend.
const SPACE_ID = import.meta.env.VITE_SPACE_ID
const POLL_MS = 1500
const OFFLINE = 'Cannot reach the server. Is the backend running?'

// Order of the tracks every engine resolves with.
export const STEMS = ['instrumental', 'vocals']

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function readError(response) {
  try {
    const body = await response.json()
    return body.detail || `Request failed (${response.status})`
  } catch {
    return `Request failed (${response.status})`
  }
}

async function request(path, options) {
  let response
  try {
    response = await fetch(`${API_URL}${path}`, options)
  } catch {
    throw new Error(OFFLINE)
  }
  if (!response.ok) throw new Error(await readError(response))
  return response
}

// fetch cannot report upload progress, so the upload goes through XHR.
function uploadSong(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const form = new FormData()
    form.append('file', file)
    xhr.open('POST', `${API_URL}/api/upload`)
    xhr.responseType = 'json'
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response.job_id)
      else reject(new Error(xhr.response?.detail || `Upload failed (${xhr.status})`))
    }
    xhr.onerror = () => reject(new Error(OFFLINE))
    xhr.send(form)
  })
}

function describeJob(job) {
  if (job.status === 'queued') return { text: 'Waiting in queue…', progress: null }
  if (job.progress === 0) return { text: 'Loading model…', progress: 0 }
  if (job.progress >= 99) return { text: 'Saving files…', progress: job.progress }
  return { text: `Removing vocals… ${job.progress}%`, progress: job.progress }
}

async function processOnBackend(file, report, isStale) {
  report({ text: 'Uploading… 0%', progress: 0 })
  const jobId = await uploadSong(file, (percent) =>
    report({ text: `Uploading… ${percent}%`, progress: percent }),
  )

  for (;;) {
    if (isStale()) return null
    const job = await (await request(`/api/status/${jobId}`)).json()
    if (job.status === 'error') throw new Error(job.error || 'Separation failed.')
    if (job.status === 'done') break
    report(describeJob(job))
    await sleep(POLL_MS)
  }

  report({ text: 'Fetching results…', progress: null })
  const blobs = await Promise.all(
    STEMS.map(async (stem) => (await request(`/api/download/${jobId}/${stem}`)).blob()),
  )
  // Results now live in browser memory, so the server copy can go.
  request(`/api/jobs/${jobId}`, { method: 'DELETE' }).catch(() => {})
  return blobs
}

async function processOnSpace(file, report, isStale) {
  report({ text: 'Connecting…', progress: null })
  const { Client, handle_file } = await import('@gradio/client')
  let client
  try {
    client = await Client.connect(SPACE_ID, {
      events: ['data', 'status'],
      // A Space that has been idle sleeps and takes a while to start again.
      status_callback: (space) => {
        if (space.status !== 'running') report({ text: 'Server is waking up…', progress: null })
      },
    })
  } catch {
    throw new Error('Cannot reach the server. Try again in a minute.')
  }

  report({ text: 'Uploading…', progress: null })
  const job = client.submit('/separate', { song: handle_file(file) })
  let files = null
  for await (const message of job) {
    if (isStale()) {
      job.cancel()
      return null
    }
    if (message.type === 'data') {
      files = message.data
    } else if (message.type === 'status') {
      if (message.stage === 'error') {
        const detail = typeof message.message === 'string' ? message.message : ''
        throw new Error(detail || 'Separation failed.')
      }
      const step = message.progress_data?.[0]?.desc
      if (step) report({ text: `${step}…`, progress: null })
      else if (message.position > 0)
        report({ text: `Waiting in queue (position ${message.position + 1})…`, progress: null })
    }
  }
  if (!files) throw new Error('Separation failed.')

  report({ text: 'Fetching results…', progress: null })
  return Promise.all(files.map(async (item) => (await fetch(item.url)).blob()))
}

/**
 * Split a song. Resolves with one Blob per entry of STEMS, or null when `isStale()`
 * turned true meanwhile. `report({ text, progress })` receives status updates;
 * `progress` is 0-100, or null when it is not known.
 */
export const processSong = SPACE_ID ? processOnSpace : processOnBackend
