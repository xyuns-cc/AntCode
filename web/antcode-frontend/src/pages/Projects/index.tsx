import type React from 'react'
import { memo } from 'react'
import { Routes, Route } from 'react-router-dom'
import ProjectList from './ProjectList'
import ProjectDetail from './ProjectDetail'
import ProjectFileManager from '../ProjectFileManager'

const Projects: React.FC = memo(() => {
  return (
    <Routes>
      <Route index element={<ProjectList />} />
      <Route path=":id" element={<ProjectDetail />} />

      <Route path=":id/files" element={<ProjectFileManager />} />
    </Routes>
  )
})

export default Projects
