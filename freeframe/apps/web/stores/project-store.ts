import { create } from 'zustand'
import { Project, Asset } from '@/types'
import { api } from '@/lib/api'

interface ProjectState {
  projects: Project[]
  currentProject: Project | null
  assets: Asset[]
  isLoading: boolean
  setCurrentProject: (project: Project) => void
  fetchProjects: () => Promise<void>
  fetchAssets: (projectId: string) => Promise<void>
}

export const useProjectStore = create<ProjectState>()((set) => ({
  projects: [],
  currentProject: null,
  assets: [],
  isLoading: false,

  setCurrentProject: (project: Project) => {
    set({ currentProject: project })
  },

  fetchProjects: async () => {
    set({ isLoading: true })
    try {
      const projects = await api.get<Project[]>('/projects')
      set({ projects })
    } finally {
      set({ isLoading: false })
    }
  },

  fetchAssets: async (projectId: string) => {
    set({ isLoading: true })
    try {
      const assets = await api.get<Asset[]>(`/projects/${projectId}/assets`)
      set({ assets })
    } finally {
      set({ isLoading: false })
    }
  },
}))
