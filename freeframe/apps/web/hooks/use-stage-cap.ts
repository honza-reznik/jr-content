'use client'

import { useCallback, useEffect, useState, type RefObject } from 'react'

/**
 * Height assumed for the player's controls until the player has measured them:
 * the 12px progress wrapper and the 48px transport row, with no comment markers.
 */
export const CONTROLS_FALLBACK_PX = 60
/** The stage never shrinks below this, whatever is left. */
export const MIN_STAGE_PX = 80

/** Room left for the media above the sheet and the player's own controls. */
export function stageCapPx(rootHeight: number, sheetHeight: number, controlsHeight: number): number {
  return Math.max(MIN_STAGE_PX, Math.round(rootHeight - sheetHeight - controlsHeight))
}

/**
 * Caps the media stage on a portrait phone so the player's controls stay above
 * the comment sheet (JR Content P1-B26, N-k1e-07).
 *
 * The sheet OVERLAYS the media, so the media has to fit in what is left above
 * it. That room used to be computed with a fixed 60px for the controls, but the
 * progress area grows a row of comment markers (~27px) once a version has a
 * comment, so with one comment the sheet covered Play. The player now reports
 * the measured height of its controls (`onControlsHeight`), and the root is
 * observed as well, so a toolbar or viewport that changes size re-caps it.
 */
export function useStageCap(rootRef: RefObject<HTMLElement>, useSheet: boolean, sheetHeight: number) {
  const [controlsHeight, setControlsHeight] = useState(CONTROLS_FALLBACK_PX)
  // Stable, so the player's effect does not re-subscribe on every render.
  const onControlsHeight = useCallback((px: number) => {
    if (Number.isFinite(px) && px > 0) setControlsHeight(Math.ceil(px))
  }, [])

  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    if (!useSheet) {
      root.style.removeProperty('--ff-stage-max')
      return
    }
    const apply = () => {
      root.style.setProperty('--ff-stage-max', `${stageCapPx(root.clientHeight, sheetHeight, controlsHeight)}px`)
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(apply)
    observer.observe(root)
    return () => observer.disconnect()
  }, [rootRef, useSheet, sheetHeight, controlsHeight])

  return { controlsHeight, onControlsHeight }
}
