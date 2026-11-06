import fs from 'node:fs/promises'
import path from 'node:path'

const url = (process.env.YURLAPS_SUPABASE_URL || '').replace(/\/$/, '')
const key = process.env.YURLAPS_SUPABASE_SERVICE_ROLE_KEY
const bucket = process.env.YURLAPS_RELEASE_BUCKET || 'yurlaps-releases'
const dir = process.env.YURLAPS_RELEASE_DIR || 'dist-electron'
const version = process.env.YURLAPS_RELEASE_VERSION || ''
if (!url || !key || !version) throw new Error('Supabase release variables are incomplete')

const names = [`YurLaps-Setup-${version}.exe`, `YurLaps-Setup-${version}.exe.blockmap`, 'latest.yml']
const files = []
for (const name of names) {
  const file = path.join(dir, name)
  try { const data = await fs.readFile(file); files.push({ name, data }) }
  catch { if (name.endsWith('.exe')) throw new Error(`Missing release asset: ${file}`) }
}
const base = `${url}/storage/v1/object/${bucket}`
for (const file of files) {
  const response = await fetch(`${base}/${encodeURIComponent(file.name)}`, {
    method: 'POST', headers: { apikey: key, Authorization: `Bearer ${key}`,
      'Content-Type': 'application/octet-stream', 'x-upsert': 'true' }, body: file.data,
  })
  if (!response.ok) throw new Error(`Upload ${file.name} failed: ${response.status} ${await response.text()}`)
}
const installer = files.find(f => f.name.endsWith('.exe'))
const manifest = { version, published_at: new Date().toISOString(), notes: process.env.YURLAPS_RELEASE_NOTES || '',
  installer: { name: installer.name, url: `/releases/${encodeURIComponent(installer.name)}`, size: installer.data.length } }
const response = await fetch(`${base}/manifest.json`, {
  method: 'POST', headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json', 'x-upsert': 'true' },
  body: JSON.stringify(manifest, null, 2),
})
if (!response.ok) throw new Error(`Upload manifest failed: ${response.status} ${await response.text()}`)
console.log(`Published YurLaps ${version} to ${bucket}`)
