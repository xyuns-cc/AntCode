import { render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import RuleProjectForm from './RuleProjectForm'

vi.mock('@/contexts/ThemeContext', () => ({
  useThemeContext: () => ({}),
}))

vi.mock('./RuleSelector', () => ({
  default: () => null,
}))

describe('RuleProjectForm edit payload', () => {
  it('emits false when an existing rule disables resume', async () => {
    const onDataChange = vi.fn()

    render(
      <RuleProjectForm
        isEdit
        initialData={{ resume_enabled: false }}
        onDataChange={onDataChange}
        onSubmit={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(onDataChange).toHaveBeenCalledWith(expect.objectContaining({ resume_enabled: false }))
    })
  })
})
