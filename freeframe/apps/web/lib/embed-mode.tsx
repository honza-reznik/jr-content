'use client'

import { createContext, useContext } from 'react'

const EmbedModeContext = createContext(false)

export const EmbedModeProvider = EmbedModeContext.Provider

export function useEmbedMode(): boolean {
  return useContext(EmbedModeContext)
}

export function shouldShowEmbedCommentsPanel(
  isEmbed: boolean,
  isLandscape: boolean,
  sidebarOpen: boolean,
  useSheet: boolean,
): boolean {
  return !useSheet && (sidebarOpen || (isEmbed && isLandscape))
}
