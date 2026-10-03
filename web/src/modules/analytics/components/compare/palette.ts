import { tc } from '../../../../core/theme';
/**
 * Slot colours for the comparison tabs.
 *
 * Position N in a comparison always gets colour N — the selector chip, the
 * summary card's top border, the table header, every chart series and the radar
 * all key off the same index. That is what lets you read "the amber one" across
 * the whole page without consulting a legend.
 *
 * Must stay in the same order as RadarCompareChart's internal PALETTE, which
 * assigns colours by entity index rather than by name.
 */
export const COMPARE_COLORS = [tc('#3b82f6'), tc('#10b981'), tc('#f59e0b'), tc('#ef4444')];

/** How many entities either tab will compare at once. */
export const MAX_COMPARE = 4;
