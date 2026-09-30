import { useState } from 'react'
import {
  AppRoot,
  Button,
  Cell,
  List,
  Section,
  Placeholder,
} from '@telegram-apps/telegram-ui'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'

function App() {
  const [count, setCount] = useState(0)

  return (
    <AppRoot>
      <List style={{ padding: 16, maxWidth: 480, margin: '0 auto' }}>
        <Placeholder
          header="frontend-kit"
          description="Telegram UI + Font Awesome baseline for mini-apps"
        >
          <FontAwesomeIcon icon="rocket" style={{ fontSize: 48 }} />
        </Placeholder>

        <Section header="Telegram UI" footer="@telegram-apps/telegram-ui">
          <Cell
            before={<FontAwesomeIcon icon={['fab', 'telegram']} />}
            subtitle="Platform + theme auto-detected by AppRoot"
          >
            Components
          </Cell>
          <Cell
            before={<FontAwesomeIcon icon="hand-pointer" />}
            after={<span>{count}</span>}
          >
            Counter
          </Cell>
        </Section>

        <Section header="Actions">
          <div style={{ padding: 16, display: 'flex', gap: 8 }}>
            <Button
              size="m"
              before={<FontAwesomeIcon icon="plus" />}
              onClick={() => setCount((c) => c + 1)}
            >
              Increment
            </Button>
            <Button
              size="m"
              mode="bezeled"
              before={<FontAwesomeIcon icon={['far', 'trash-can']} />}
              onClick={() => setCount(0)}
            >
              Reset
            </Button>
          </div>
        </Section>
      </List>
    </AppRoot>
  )
}

export default App
