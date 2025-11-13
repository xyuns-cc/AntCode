import { execSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { readdirSync, statSync, existsSync, copyFileSync, mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

const SCRIPT_DIR = resolve(new URL('.', import.meta.url).pathname)
const PROJECT_ROOT = resolve(SCRIPT_DIR, '..')
const DIST_DIR = resolve(PROJECT_ROOT, 'dist')

function run(command) {
  execSync(command, { stdio: 'inherit', shell: true })
}

function ensureDistDir() {
  try {
    mkdirSync(DIST_DIR, { recursive: true })
  } catch (error) {
    if (error.code !== 'EEXIST') {
      throw error
    }
  }
}

function findLatestStatsFile() {
  const tmpRoot = tmpdir()
  const entries = readdirSync(tmpRoot, { withFileTypes: true })

  let latestFile = null
  let latestMtime = 0

  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.startsWith('tmp-')) {
      continue
    }

    const candidate = join(tmpRoot, entry.name, 'stats.html')
    if (!existsSync(candidate)) {
      continue
    }

    const stats = statSync(candidate)
    if (stats.mtimeMs > latestMtime) {
      latestMtime = stats.mtimeMs
      latestFile = candidate
    }
  }

  return latestFile
}

function main() {
  run('npm run build')
  run('npx vite-bundle-visualizer --open false --template treemap')

  const statsFile = findLatestStatsFile()
  if (!statsFile) {
    console.error('未能在临时目录中找到 stats.html，请检查可用的临时文件。')
    process.exitCode = 1
    return
  }

  ensureDistDir()
  const targetPath = join(DIST_DIR, 'bundle-stats.html')
  copyFileSync(statsFile, targetPath)
  console.log(`Bundle 分析报告已生成: ${targetPath}`)
}

main()

