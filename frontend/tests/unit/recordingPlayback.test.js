import { readFileSync } from 'node:fs'
import path from 'node:path'
import { parse } from '@vue/compiler-sfc'
import { baseParse } from '@vue/compiler-dom'

function audioElements(node) {
  return [
    ...(node.tag === 'audio' ? [node] : []),
    ...(node.children || []).flatMap(audioElements),
  ]
}

describe('recording playback', () => {
  it.each(['Activities/AudioPlayer.vue', 'Modals/CallLogDetailModal.vue'])(
    '%s allows private S3 redirects without requiring bucket CORS',
    (component) => {
      const source = readFileSync(
        path.resolve(import.meta.dirname, '../../src/components', component),
        'utf8',
      )
      const { descriptor } = parse(source)
      const elements = audioElements(baseParse(descriptor.template.content))

      expect(elements).toHaveLength(1)
      expect(
        elements[0].props.some(
          (prop) =>
            prop.name === 'crossorigin' || prop.arg?.content === 'crossorigin',
        ),
      ).toBe(false)
      expect(
        elements[0].props.some(
          (prop) => prop.name === 'bind' && prop.arg?.content === 'src',
        ),
      ).toBe(true)
    },
  )
})
