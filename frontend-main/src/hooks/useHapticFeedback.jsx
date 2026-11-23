import { useCallback } from 'react';

export const useHapticFeedback = () => {
  const triggerHaptic = useCallback((type = 'light') => {
    // Check if device supports haptic feedback
    if (!navigator.vibrate) {
      return;
    }

    // Different vibration patterns for different feedback types
    const patterns = {
      light: [10],           // Short, light tap
      medium: [20],          // Medium tap
      heavy: [30],           // Heavy tap
      success: [10, 50, 10], // Success pattern
      error: [30, 50, 30],   // Error pattern
      warning: [20, 30, 20], // Warning pattern
      selection: [5],        // Selection feedback
      navigation: [15],      // Navigation feedback
      longPress: [50],       // Long press feedback
      doubleTap: [10, 30, 10], // Double tap feedback
    };

    const pattern = patterns[type] || patterns.light;

    try {
      navigator.vibrate(pattern);
    } catch (error) {
      console.warn('Haptic feedback not supported:', error);
    }
  }, []);

  const light = useCallback(() => triggerHaptic('light'), [triggerHaptic]);
  const medium = useCallback(() => triggerHaptic('medium'), [triggerHaptic]);
  const heavy = useCallback(() => triggerHaptic('heavy'), [triggerHaptic]);
  const success = useCallback(() => triggerHaptic('success'), [triggerHaptic]);
  const error = useCallback(() => triggerHaptic('error'), [triggerHaptic]);
  const warning = useCallback(() => triggerHaptic('warning'), [triggerHaptic]);
  const selection = useCallback(() => triggerHaptic('selection'), [triggerHaptic]);
  const navigation = useCallback(() => triggerHaptic('navigation'), [triggerHaptic]);
  const longPress = useCallback(() => triggerHaptic('longPress'), [triggerHaptic]);
  const doubleTap = useCallback(() => triggerHaptic('doubleTap'), [triggerHaptic]);

  return {
    triggerHaptic,
    light,
    medium,
    heavy,
    success,
    error,
    warning,
    selection,
    navigation,
    longPress,
    doubleTap,
  };
};

export default useHapticFeedback;
