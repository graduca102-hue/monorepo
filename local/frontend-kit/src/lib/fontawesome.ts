// Font Awesome bootstrap.
// Imported once from main.tsx. Disables runtime <style> injection (we ship the
// CSS ourselves below) and registers the free icon packs into the global
// library so components can reference icons by string name, e.g. icon="rocket".
import { config, library } from '@fortawesome/fontawesome-svg-core'
import '@fortawesome/fontawesome-svg-core/styles.css'
import { fas } from '@fortawesome/free-solid-svg-icons'
import { far } from '@fortawesome/free-regular-svg-icons'
import { fab } from '@fortawesome/free-brands-svg-icons'

config.autoAddCss = false

library.add(fas, far, fab)
