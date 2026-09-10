import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(process.argv[2] || 'frontend/src')
const unresolved = []
const candidates = value => [value, `${value}.ts`, `${value}.vue`, path.join(value, 'index.ts')]

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name)
    if (entry.isDirectory()) walk(filename)
    if (!entry.isFile() || !/\.(ts|vue)$/.test(filename)) continue
    const source = fs.readFileSync(filename, 'utf8')
    for (const match of source.matchAll(/(?:from\s+|import\s*)['"]([^'"]+)['"]/g)) {
      const specifier = match[1]
      if (!specifier.startsWith('@/') && !specifier.startsWith('./') && !specifier.startsWith('../')) continue
      const resolved = specifier.startsWith('@/')
        ? path.join(root, specifier.slice(2))
        : path.resolve(path.dirname(filename), specifier)
      if (!candidates(resolved).some(fs.existsSync)) {
        unresolved.push(`${path.relative(root, filename)} -> ${specifier}`)
      }
    }
  }
}

walk(root)
if (unresolved.length) {
  console.error(unresolved.join('\n'))
  process.exit(1)
}
console.log('All local frontend imports resolve.')
